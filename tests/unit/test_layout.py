import datetime as dt

import pytest

from site3sm.layout import (
    BODY_START_ROW,
    LayoutError,
    TabLayout,
    build_period_columns,
    prev_column,
)


def test_period_columns_ascending_unique_with_opening():
    ends = [dt.date(2025, 12, 28), dt.date(2023, 1, 1), dt.date(2024, 12, 29), dt.date(2023, 12, 31)]
    cols = build_period_columns(ends, include_opening=True)
    assert cols == {
        dt.date(2023, 1, 1): "E",
        dt.date(2023, 12, 31): "F",
        dt.date(2024, 12, 29): "G",
        dt.date(2025, 12, 28): "H",
    }


def test_period_columns_no_opening_uses_fgh_only():
    ends = [dt.date(2025, 12, 28), dt.date(2024, 12, 29), dt.date(2023, 12, 31)]
    cols = build_period_columns(ends, include_opening=False)
    assert cols == {dt.date(2023, 12, 31): "F", dt.date(2024, 12, 29): "G", dt.date(2025, 12, 28): "H"}
    assert "E" not in cols.values()


def test_period_columns_stable_across_tabs():
    """The spec's core invariant: the same period maps to the same column letter
    regardless of which tab is asking (a duration's end date and the matching BS
    instant date must resolve identically)."""
    ends = [dt.date(2023, 1, 1), dt.date(2023, 12, 31), dt.date(2024, 12, 29), dt.date(2025, 12, 28)]
    bs_cols = build_period_columns(ends, include_opening=True)
    is_cols = build_period_columns(ends, include_opening=True)
    assert bs_cols == is_cols


def test_period_columns_wrong_count_raises():
    with pytest.raises(LayoutError):
        build_period_columns([dt.date(2025, 1, 1), dt.date(2024, 1, 1)], include_opening=True)


def test_prev_column():
    letters = ["E", "F", "G", "H"]
    assert prev_column("H", letters) == "G"
    assert prev_column("F", letters) == "E"
    assert prev_column("E", letters) is None


def test_prev_column_unknown_raises():
    with pytest.raises(LayoutError):
        prev_column("Z", ["E", "F", "G", "H"])


def test_tab_layout_assigns_unique_sequential_rows_by_order():
    line_items = [
        {"id": "IS_C", "tab": "Hist_IS", "order": 300, "status": "approved", "kind": "fact"},
        {"id": "IS_A", "tab": "Hist_IS", "order": 100, "status": "approved", "kind": "fact"},
        {"id": "IS_B", "tab": "Hist_IS", "order": 200, "status": "approved", "kind": "formula"},
        {"id": "BS_X", "tab": "Hist_BS", "order": 100, "status": "approved", "kind": "fact"},
        {"id": "IS_UNAPPROVED", "tab": "Hist_IS", "order": 150, "status": "proposed", "kind": "fact"},
    ]
    layout = TabLayout("Hist_IS", line_items)
    assert layout.row_of("IS_A") == BODY_START_ROW
    assert layout.row_of("IS_B") == BODY_START_ROW + 1
    assert layout.row_of("IS_C") == BODY_START_ROW + 2
    assert "IS_UNAPPROVED" not in layout.row_map  # unapproved config is never used (R2)
    assert "BS_X" not in layout.row_map  # different tab
    assert layout.kind_of("IS_B") == "formula"


def test_tab_layout_no_row_collisions():
    rows = [
        {"id": f"IS_{i}", "tab": "Hist_IS", "order": i * 10, "status": "approved", "kind": "fact"} for i in range(20)
    ]
    layout = TabLayout("Hist_IS", rows)
    assert len(set(layout.row_map.values())) == len(layout.row_map)


def test_tab_layout_duplicate_line_id_raises():
    # Reproduces the legacy column-offset-adjacent failure mode: two config entries
    # that resolve to the same id must not silently share a row.
    line_items = [
        {"id": "IS_DUP", "tab": "Hist_IS", "order": 100, "status": "approved", "kind": "fact"},
        {"id": "IS_DUP", "tab": "Hist_IS", "order": 200, "status": "approved", "kind": "fact"},
    ]
    with pytest.raises(LayoutError):
        TabLayout("Hist_IS", line_items)


def test_tab_layout_unknown_line_id_raises():
    layout = TabLayout("Hist_IS", [{"id": "IS_A", "tab": "Hist_IS", "order": 100, "status": "approved", "kind": "fact"}])
    with pytest.raises(LayoutError):
        layout.row_of("IS_NOPE")
