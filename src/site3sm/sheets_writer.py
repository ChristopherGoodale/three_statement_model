"""Build the write set for a tab and either dry-run it to CSV or write it to a
live Google Sheet (spec Sec 9.7). Formatting is always derived from cell kind,
never assigned by hand.
"""
from __future__ import annotations

import csv
import datetime as dt
import pathlib

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials

from site3sm.formulas import cell_kind, resolve_formula
from site3sm.layout import HEADER_ROWS, LABEL_COL, LINE_ID_COL, UNIT_COL, TabLayout

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

FONT_COLOR = {
    "fact": {"red": 0.06, "green": 0.24, "blue": 0.66},  # blue
    "same_tab_formula": {"red": 0.0, "green": 0.0, "blue": 0.0},  # black
    "cross_tab_formula": {"red": 0.0, "green": 0.45, "blue": 0.2},  # green
    "human_input": {"red": 0.0, "green": 0.0, "blue": 0.0},
    "header": {"red": 0.0, "green": 0.0, "blue": 0.0},
}
FILL_COLOR = {"human_input": {"red": 1.0, "green": 0.95, "blue": 0.6}}  # yellow


class WriteSetError(ValueError):
    pass


def _scale(unit: str, value: float, settings) -> float:
    if unit == "USD" and settings.render.usd_display_scale:
        return value / settings.render.usd_display_scale
    if unit == "shares" and settings.render.shares_display_scale:
        return value / settings.render.shares_display_scale
    return value


def build_write_set(
    tab: str,
    line_items: list[dict],
    curated: pd.DataFrame,
    layouts: dict[str, TabLayout],
    period_columns: dict,
    active_letters: list[str],
    settings,
) -> dict[tuple[str, str], dict]:
    """Returns {(tab, a1): {"kind":..., "value_or_formula":..., "owner": "code"}}.
    Raises WriteSetError on any duplicate (tab, a1) target (spec Sec 9.6 collision guard).
    """
    layout = layouts[tab]
    write_set: dict[tuple[str, str], dict] = {}

    def put(a1: str, kind: str, value_or_formula, owner: str = "code") -> None:
        key = (tab, a1)
        if key in write_set:
            raise WriteSetError(f"collision: {tab}!{a1} already targeted by another cell")
        write_set[key] = {"kind": kind, "value_or_formula": value_or_formula, "owner": owner}

    # Column E is the opening-balance-sheet-only column (spec Sec 9.6): it only
    # applies to tabs that actually carry an instant fact at that date (BS/EQ).
    # A duration-only tab like Hist_IS/Hist_CF has no "opening year" activity to
    # show there -- period_columns is shared/global across every tab, so this has
    # to be filtered per tab rather than assumed. Missing this produced spurious
    # "=E7-E8"-style formulas evaluating against blank cells (silently reading as
    # 0) the first time this ran live.
    tab_has_instant_line = any(
        li.get("tab") == tab and li.get("status") == "approved" and li.get("period_type") == "instant"
        for li in line_items
    )
    tab_period_columns = period_columns if tab_has_instant_line else {d: c for d, c in period_columns.items() if c != "E"}

    # header rows: period label/start/end/days/AE-flag, identical shape on every tab
    tab_curated = curated[curated["statement"] == tab.replace("Hist_", "")]
    for period_end, col in tab_period_columns.items():
        rows_here = tab_curated[tab_curated["period_end"] == period_end]
        period_start = rows_here["period_start"].dropna().iloc[0] if rows_here["period_start"].notna().any() else None

        put(f"{col}{HEADER_ROWS['HDR_FISCAL_YEAR_LABEL']}", "header", f"FY{period_end.year}")
        put(f"{col}{HEADER_ROWS['HDR_PERIOD_START']}", "header", period_start)
        put(f"{col}{HEADER_ROWS['HDR_PERIOD_END']}", "header", period_end)
        end_row, start_row = HEADER_ROWS["HDR_PERIOD_END"], HEADER_ROWS["HDR_PERIOD_START"]
        put(f"{col}{HEADER_ROWS['HDR_DAYS']}", "same_tab_formula", f"={col}{end_row}-{col}{start_row}+1")
        put(f"{col}{HEADER_ROWS['HDR_AE_FLAG']}", "header", "A")

    # body rows
    for li in line_items:
        if li.get("tab") != tab or li.get("status") != "approved":
            continue
        row = layout.row_of(li["id"])
        kind = li.get("kind", "fact")

        put(f"{LABEL_COL}{row}", "header" if kind == "header" else "fact", li["label"])
        if kind == "header":
            continue  # section header rows carry no line_id/unit/data cells

        put(f"{LINE_ID_COL}{row}", "fact", li["id"])
        put(f"{UNIT_COL}{row}", "fact", li.get("unit", ""))

        if li.get("owner") == "human":
            # Human-owned forecast inputs (Phase 6+) are excluded from the write set
            # entirely -- spec Sec 9.7: human-owned ranges are never read-modify-
            # written. Their one-time yellow-fill setup is a tab-creation-time
            # concern, not something a regular build run touches.
            # TODO(Phase 6): clear_code_owned_range currently clears the whole tab
            # range on every write; once a tab actually has human-owned cells this
            # needs to become a targeted clear that skips them.
            continue

        # A single tab (EQ especially) can mix instant- and duration-typed lines,
        # so this is decided per line, not just per tab: a duration fact has no
        # value for the opening-balance-sheet-only column.
        line_period_columns = period_columns if li.get("period_type") == "instant" else tab_period_columns
        for period_end, col in line_period_columns.items():
            if kind == "formula":
                formula = resolve_formula(li["template"], tab, col, layouts, active_letters)
                fkind = "cross_tab_formula" if cell_kind(li["template"], tab) == "cross_tab" else "same_tab_formula"
                put(f"{col}{row}", fkind, formula)
                continue

            match = curated[(curated["line_id"] == li["id"]) & (curated["period_end"] == period_end)]
            if len(match) == 0:
                continue  # allow_missing line, genuinely absent this period -- blank, not zero (R3/R1)
            value = _scale(li.get("unit", "USD"), float(match.iloc[0]["value"]), settings)
            put(f"{col}{row}", "fact", value)

    return write_set


def write_dry_run(write_set: dict[tuple[str, str], dict], reports_dir: str | pathlib.Path) -> pathlib.Path:
    reports_dir = pathlib.Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"render_plan_{dt.date.today().strftime('%Y%m%d')}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["tab", "a1", "kind", "value_or_formula", "owner"])
        for (tab, a1), cell in sorted(write_set.items()):
            writer.writerow([tab, a1, cell["kind"], cell["value_or_formula"], cell["owner"]])
    return path


class SheetsWriter:
    def __init__(self, credentials_path: str | pathlib.Path):
        creds = Credentials.from_service_account_file(str(credentials_path), scopes=SCOPES)
        self.client = gspread.authorize(creds)

    def ensure_tab(self, spreadsheet, tab: str):
        try:
            return spreadsheet.worksheet(tab)
        except gspread.WorksheetNotFound:
            return spreadsheet.add_worksheet(title=tab, rows=200, cols=26)

    def clear_code_owned_range(self, worksheet, max_row: int) -> None:
        worksheet.batch_clear([f"A1:H{max_row}"])

    def write(self, spreadsheet_id: str, write_set: dict[tuple[str, str], dict]) -> None:
        spreadsheet = self.client.open_by_key(spreadsheet_id)
        tabs = sorted({tab for tab, _a1 in write_set})
        worksheets = {}
        for tab in tabs:
            ws = self.ensure_tab(spreadsheet, tab)
            worksheets[tab] = ws
            max_row = max(int("".join(c for c in a1 if c.isdigit())) for (t, a1) in write_set if t == tab)
            self.clear_code_owned_range(ws, max_row)

        value_data = []
        format_requests = []
        for (tab, a1), cell in write_set.items():
            ws = worksheets[tab]
            raw = cell["value_or_formula"]
            api_value = raw.isoformat() if isinstance(raw, dt.date) else raw
            value_data.append({"range": f"'{tab}'!{a1}", "values": [[api_value]]})
            fmt = {}
            color = FONT_COLOR.get(cell["kind"])
            if color:
                fmt.setdefault("textFormat", {})["foregroundColor"] = color
            if cell["kind"] == "header":
                fmt.setdefault("textFormat", {})["bold"] = True
            fill = FILL_COLOR.get(cell["kind"])
            if fill:
                fmt["backgroundColor"] = fill
            if fmt:
                format_requests.append(
                    {
                        "repeatCell": {
                            "range": _a1_to_grid_range(ws.id, a1),
                            "cell": {"userEnteredFormat": fmt},
                            "fields": "userEnteredFormat(textFormat,backgroundColor)",
                        }
                    }
                )

        spreadsheet.values_batch_update({"valueInputOption": "USER_ENTERED", "data": value_data})
        if format_requests:
            spreadsheet.batch_update({"requests": format_requests})

        freeze_requests = [
            {
                "updateSheetProperties": {
                    "properties": {"sheetId": worksheets[tab].id, "gridProperties": {"frozenRowCount": 5, "frozenColumnCount": 4}},
                    "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
                }
            }
            for tab in tabs
        ]
        spreadsheet.batch_update({"requests": freeze_requests})


def _col_to_index(col: str) -> int:
    idx = 0
    for ch in col:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def _a1_to_grid_range(sheet_id: int, a1: str) -> dict:
    col = "".join(c for c in a1 if c.isalpha())
    row = int("".join(c for c in a1 if c.isdigit()))
    col_idx = _col_to_index(col)
    return {
        "sheetId": sheet_id,
        "startRowIndex": row - 1,
        "endRowIndex": row,
        "startColumnIndex": col_idx,
        "endColumnIndex": col_idx + 1,
    }
