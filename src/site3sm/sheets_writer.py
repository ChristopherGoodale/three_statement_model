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

E_ELIGIBLE_TABS = {"Hist_BS", "Hist_EQ"}  # spec Sec 9.6: column E is "BS/EQ only; blank on IS/CF"


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

    # Column E is the opening-balance-sheet-only column, and spec Sec 9.6 is
    # explicit that it is a per-TAB rule -- "BS/EQ only; blank on IS/CF" -- not
    # something inferred per line. That matters specifically for Hist_CF's
    # beginning/ending-cash lines: they're instant-typed facts (correctly, for
    # Phase 3 selection), but the spec still wants them rendered on CF's normal
    # F/G/H axis, never E (a per-line "is this an instant fact" rule would have
    # incorrectly pulled them onto a 4th column CF was never meant to have).
    # Duration-typed lines on an E-eligible tab (e.g. Hist_EQ's net-income-into-RE
    # row) still end up blank at E on their own, simply because no duration fact
    # exists at the opening instant -- no separate exclusion needed for those.
    tab_period_columns = period_columns if tab in E_ELIGIBLE_TABS else {d: c for d, c in period_columns.items() if c != "E"}

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

        for idx, (period_end, col) in enumerate(tab_period_columns.items()):
            # spec Sec 10.1: Hist_CF's beginning-cash line is a fact for the
            # earliest historical column and a formula (prior period's ending
            # cash) for every column after that -- a per-cell, not per-line, kind.
            effective_kind = "fact" if (kind == "fact_then_formula" and idx == 0) else kind

            if effective_kind in ("formula", "fact_then_formula"):
                formula = resolve_formula(li["template"], tab, col, layouts, active_letters)
                fkind = "cross_tab_formula" if cell_kind(li["template"], tab) == "cross_tab" else "same_tab_formula"
                put(f"{col}{row}", fkind, formula)
                continue

            lookup_period_end = period_end
            if kind == "fact_then_formula" and idx == 0:
                # "Beginning" at the earliest column means cash at the START of
                # that fiscal year, i.e. the instant BEFORE this column's own
                # end date -- not cash AT this column's own date (which is this
                # same concept's *ending* balance for that year). CF_BEGINNING
                # and CF_ENDING are the same underlying instant fact reused
                # across two presentation rows (same subtlety as the
                # begin/end-rollforward fix in validate.py), so looking this
                # column's own date up here would silently return the ending
                # balance instead. Look at the full (untrimmed-by-tab) period
                # axis for the instant immediately prior.
                all_dates = sorted(period_columns)
                pos = all_dates.index(period_end)
                if pos > 0:
                    lookup_period_end = all_dates[pos - 1]

            match = curated[(curated["line_id"] == li["id"]) & (curated["period_end"] == lookup_period_end)]
            if len(match) == 0:
                continue  # allow_missing line, genuinely absent this period -- blank, not zero (R3/R1)
            value = _scale(li.get("unit", "USD"), float(match.iloc[0]["value"]), settings)
            put(f"{col}{row}", "fact", value)

    return write_set


SOURCES_COLUMNS = [
    "line_id",
    "tab",
    "label",
    "period_end",
    "value_as_filed_unscaled",
    "unit",
    "concept",
    "taxonomy_type",
    "dimensions",
    "accn",
    "form",
    "filed",
    "source",
    "recast_flag",
    "raw_path",
]
STANDARD_TAXONOMY_PREFIXES = {"us-gaap", "dei", "srt", "ifrs-full"}


def _taxonomy_type(concept: str) -> str:
    prefix = concept.split(":", 1)[0]
    return "standard" if prefix in STANDARD_TAXONOMY_PREFIXES else "custom"


def build_sources_write_set(curated: pd.DataFrame, line_items: list[dict]) -> dict[tuple[str, str], dict]:
    """Sources tab (spec Sec 10.4): one row per curated fact, rendered directly
    from historicals.parquet -- never hand-typed. Not a period-column tab; a flat
    table with its own header row.
    """
    tab = "Sources"
    labels = {li["id"]: li.get("label", "") for li in line_items}
    write_set: dict[tuple[str, str], dict] = {}

    for col_idx, header in enumerate(SOURCES_COLUMNS):
        col = chr(ord("A") + col_idx)
        write_set[(tab, f"{col}1")] = {"kind": "header", "value_or_formula": header, "owner": "code"}

    ordered = curated.sort_values(["statement", "line_id", "period_end"])
    for row_idx, (_, r) in enumerate(ordered.iterrows(), start=2):
        values = [
            r["line_id"],
            f"Hist_{r['statement']}",
            labels.get(r["line_id"], ""),
            r["period_end"],
            float(r["value"]),
            r["unit"],
            r["concept"],
            _taxonomy_type(r["concept"]),
            r["dimensions"],
            r["accn"],
            r["form"],
            r["filed"],
            r["source"],
            bool(r["recast_flag"]),
            r["raw_path"],
        ]
        for col_idx, value in enumerate(values):
            col = chr(ord("A") + col_idx)
            write_set[(tab, f"{col}{row_idx}")] = {"kind": "fact", "value_or_formula": value, "owner": "code"}

    return write_set


def build_readme_write_set(run_id: str, target_sheet: str) -> dict[tuple[str, str], dict]:
    """README tab (spec Sec 10.1): purpose, units, color key, last run ID, target
    sheet, and (once Phase 5 exists) a link to CHK_MASTER."""
    tab = "README"
    lines = [
        "SiteOne Landscape Supply, Inc. (NYSE: SITE) -- Three-Statement Model",
        "",
        "Purpose: verified historicals sourced from SEC EDGAR XBRL, rendered as live formulas with full provenance (see Sources tab).",
        "Units: USD in millions unless noted; shares in millions; EPS in $/share.",
        "Color key: blue = sourced fact | black = same-tab formula | green = cross-tab formula | yellow = human input.",
        f"Last run ID: {run_id}",
        f"Target sheet: {target_sheet}",
        "Master check: pending Phase 5 (Checks tab not built yet).",
    ]
    write_set: dict[tuple[str, str], dict] = {}
    for row_idx, text in enumerate(lines, start=1):
        write_set[(tab, f"A{row_idx}")] = {"kind": "header" if row_idx == 1 else "fact", "value_or_formula": text, "owner": "code"}
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

    def ensure_tab(self, spreadsheet, tab: str, rows: int, cols: int):
        try:
            ws = spreadsheet.worksheet(tab)
            if ws.row_count < rows or ws.col_count < cols:
                ws.resize(rows=max(ws.row_count, rows), cols=max(ws.col_count, cols))
            return ws
        except gspread.WorksheetNotFound:
            return spreadsheet.add_worksheet(title=tab, rows=rows, cols=cols)

    def clear_code_owned_range(self, worksheet, max_row: int, max_col: str = "H") -> None:
        worksheet.batch_clear([f"A1:{max_col}{max_row}"])

    def write(self, spreadsheet_id: str, write_set: dict[tuple[str, str], dict]) -> None:
        spreadsheet = self.client.open_by_key(spreadsheet_id)
        tabs = sorted({tab for tab, _a1 in write_set})
        worksheets = {}
        for tab in tabs:
            tab_a1s = [a1 for (t, a1) in write_set if t == tab]
            max_row = max(int("".join(c for c in a1 if c.isdigit())) for a1 in tab_a1s)
            max_col_idx = max(_col_to_index(_col_letters(a1)) for a1 in tab_a1s)
            max_col = _index_to_col(max(max_col_idx, _col_to_index("H")))
            ws = self.ensure_tab(spreadsheet, tab, rows=max(200, max_row + 10), cols=max(26, max_col_idx + 5))
            worksheets[tab] = ws
            self.clear_code_owned_range(ws, max_row, max_col)

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


def _col_letters(a1: str) -> str:
    return "".join(c for c in a1 if c.isalpha())


def _col_to_index(col: str) -> int:
    idx = 0
    for ch in col:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def _index_to_col(idx: int) -> str:
    idx += 1
    letters = ""
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


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
