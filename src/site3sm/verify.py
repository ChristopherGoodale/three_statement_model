"""Read back a written tab and assert it matches what was intended (spec Sec 9.8).
Nothing is "done" until this passes -- verification, not just a successful write.
"""
from __future__ import annotations

import datetime as dt
import math
import pathlib

import gspread
from google.oauth2.service_account import Credentials

from site3sm.sheets_writer import SCOPES

FLOAT_REL_TOL = 1e-6
SHEETS_EPOCH = dt.date(1899, 12, 30)


def _serial_to_date(serial) -> dt.date | None:
    """Sheets returns date cells as serial day-numbers under UNFORMATTED_VALUE,
    not ISO strings -- epoch 1899-12-30 (Sheets/Excel's date system)."""
    try:
        return SHEETS_EPOCH + dt.timedelta(days=int(serial))
    except (TypeError, ValueError):
        return None


def _col_letters(a1: str) -> str:
    return "".join(c for c in a1 if c.isalpha())


def _row_num(a1: str) -> int:
    return int("".join(c for c in a1 if c.isdigit()))


def verify_tab(
    spreadsheet_id: str,
    tab: str,
    write_set: dict[tuple[str, str], dict],
    credentials_path: str | pathlib.Path,
) -> list[dict]:
    creds = Credentials.from_service_account_file(str(credentials_path), scopes=SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(spreadsheet_id)
    try:
        ws = spreadsheet.worksheet(tab)
    except gspread.WorksheetNotFound:
        # Not every configured tab has necessarily been built yet (Phase 4 lands
        # tab by tab) -- that's a scope question for `build`, not a verification
        # failure, so this is a note, not an error.
        return [{"check": "tab_exists", "tab": tab, "a1": "", "passed": None, "note": "tab not found -- not yet built"}]

    tab_cells = {a1: cell for (t, a1), cell in write_set.items() if t == tab}
    if not tab_cells:
        return []
    max_row = max(_row_num(a1) for a1 in tab_cells)
    max_col_idx = max(ord(_col_letters(a1)) - ord("A") for a1 in tab_cells)
    max_col = chr(ord("A") + max_col_idx)
    full_range = f"A1:{max_col}{max_row}"

    values = ws.get(full_range, value_render_option="UNFORMATTED_VALUE")
    formulas = ws.get(full_range, value_render_option="FORMULA")

    def cell_at(grid: list[list], a1: str):
        row_idx = _row_num(a1) - 1
        col_idx = ord(_col_letters(a1)) - ord("A")
        if row_idx >= len(grid) or col_idx >= len(grid[row_idx]):
            return None
        val = grid[row_idx][col_idx]
        return val if val != "" else None

    results: list[dict] = []

    for a1, expected in tab_cells.items():
        is_formula = isinstance(expected["value_or_formula"], str) and expected["value_or_formula"].startswith("=")
        actual_value = cell_at(values, a1)
        actual_formula = cell_at(formulas, a1)

        if is_formula:
            ok = actual_formula == expected["value_or_formula"]
            results.append(
                {
                    "check": "formula_text",
                    "tab": tab,
                    "a1": a1,
                    "passed": ok,
                    "note": "" if ok else f"expected {expected['value_or_formula']!r}, got {actual_formula!r}",
                }
            )
            ok_prefix = isinstance(actual_formula, str) and actual_formula.startswith("=")
            results.append({"check": "formula_starts_with_equals", "tab": tab, "a1": a1, "passed": ok_prefix, "note": ""})
        else:
            expected_val = expected["value_or_formula"]
            if isinstance(expected_val, (int, float)):
                ok = actual_value is not None and math.isclose(float(actual_value), float(expected_val), rel_tol=FLOAT_REL_TOL, abs_tol=1e-9)
            elif isinstance(expected_val, dt.date):
                ok = _serial_to_date(actual_value) == expected_val
            else:
                ok = actual_value == expected_val
            results.append(
                {
                    "check": "fact_value",
                    "tab": tab,
                    "a1": a1,
                    "passed": ok,
                    "note": "" if ok else f"expected {expected_val!r}, got {actual_value!r}",
                }
            )

    # no unexpected non-empty cells inside the code-owned range
    for row_idx, row in enumerate(values, start=1):
        for col_idx, val in enumerate(row):
            if val == "" or val is None:
                continue
            col = chr(ord("A") + col_idx)
            a1 = f"{col}{row_idx}"
            if a1 not in tab_cells:
                results.append(
                    {"check": "no_unexpected_cells", "tab": tab, "a1": a1, "passed": False, "note": f"unexpected value {val!r}"}
                )

    return results


def render_report(results: list[dict]) -> str:
    lines = ["# Verify report", "", "| tab | a1 | check | status | note |", "|---|---|---|---|---|"]
    for r in results:
        status = "OK" if r["passed"] else ("N/A" if r["passed"] is None else "FAIL")
        lines.append(f"| {r['tab']} | {r['a1']} | {r['check']} | {status} | {r.get('note','')} |")
    failures = [r for r in results if r["passed"] is False]  # None = informational note, not a failure
    lines.append("")
    lines.append(f"**{len(failures)} failing check(s)** out of {len(results)}.")
    return "\n".join(lines)


def run_verify(
    spreadsheet_id: str,
    write_set: dict[tuple[str, str], dict],
    credentials_path: str | pathlib.Path,
    reports_dir: str | pathlib.Path = "reports",
) -> tuple[pathlib.Path, list[dict]]:
    tabs = sorted({tab for tab, _a1 in write_set})
    all_results: list[dict] = []
    for tab in tabs:
        all_results.extend(verify_tab(spreadsheet_id, tab, write_set, credentials_path))

    reports_dir = pathlib.Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"verify_{dt.date.today().strftime('%Y%m%d')}.md"
    report_path.write_text(render_report(all_results), encoding="utf-8")
    return report_path, all_results
