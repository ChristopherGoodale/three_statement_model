"""Column/row layout for period-based tabs (spec Sec 9.6).

Global period axis: the same period uses the same column letter on every
period-based tab. Column E is the opening-balance-sheet date; F/G/H are the 3
historical fiscal years. A single instant-date-anchored column map is shared by
every tab -- a duration fact's end date coincides in value with one of the
instant dates (the fiscal year-end), so looking it up in the same map naturally
lands it in the right column without a separate duration axis.
"""
from __future__ import annotations

import datetime as dt

LABEL_COL, LINE_ID_COL, UNIT_COL, FLAG_COL = "A", "B", "C", "D"
PERIOD_COLUMN_LETTERS = ["E", "F", "G", "H"]  # opening, FY-2, FY-1, FY0 (Phase 7 extends with I, J, ...)

HEADER_ROW_IDS = ["HDR_FISCAL_YEAR_LABEL", "HDR_PERIOD_START", "HDR_PERIOD_END", "HDR_DAYS", "HDR_AE_FLAG"]
HEADER_ROWS = {hid: i + 1 for i, hid in enumerate(HEADER_ROW_IDS)}  # rows 1-5
BODY_START_ROW = 7  # row 6 left blank as a separator


class LayoutError(ValueError):
    pass


def build_period_columns(instant_ends: list[dt.date], include_opening: bool) -> dict[dt.date, str]:
    letters = PERIOD_COLUMN_LETTERS if include_opening else PERIOD_COLUMN_LETTERS[1:]
    ordered = sorted(instant_ends)
    if len(ordered) != len(letters):
        raise LayoutError(f"expected {len(letters)} periods for the configured columns, got {len(ordered)}: {ordered}")
    return dict(zip(ordered, letters))


def prev_column(col: str, active_letters: list[str]) -> str | None:
    if col not in active_letters:
        raise LayoutError(f"column {col} is not one of the active period columns {active_letters}")
    idx = active_letters.index(col)
    return active_letters[idx - 1] if idx > 0 else None


class TabLayout:
    """Row assignment for one tab's approved line_items (spec Sec 9.6 'Row assignment').

    Two lines may never share a row -- enforced by construction (each id gets the
    next sequential row; a repeated id is a config bug and raises immediately).
    """

    def __init__(self, tab: str, line_items: list[dict]):
        self.tab = tab
        self.row_map: dict[str, int] = {}
        self.kind_map: dict[str, str] = {}
        tab_items = sorted(
            (li for li in line_items if li.get("tab") == tab and li.get("status") == "approved"),
            key=lambda li: li["order"],
        )
        row = BODY_START_ROW
        for li in tab_items:
            if li["id"] in self.row_map:
                raise LayoutError(f"duplicate line_id on {tab}: {li['id']}")
            self.row_map[li["id"]] = row
            self.kind_map[li["id"]] = li.get("kind", "fact")
            row += 1

    def row_of(self, line_id: str) -> int:
        if line_id not in self.row_map:
            raise LayoutError(f"unknown line_id {line_id!r} on tab {self.tab!r}")
        return self.row_map[line_id]

    def kind_of(self, line_id: str) -> str:
        if line_id not in self.kind_map:
            raise LayoutError(f"unknown line_id {line_id!r} on tab {self.tab!r}")
        return self.kind_map[line_id]
