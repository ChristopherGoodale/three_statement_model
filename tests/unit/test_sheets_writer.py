import datetime as dt
from types import SimpleNamespace

import pandas as pd

from site3sm.layout import TabLayout, build_period_columns
from site3sm.sheets_writer import build_write_set, write_dry_run

FAKE_SETTINGS = SimpleNamespace(render=SimpleNamespace(usd_display_scale=1_000_000, shares_display_scale=1_000_000))

LINE_ITEMS = [
    {
        "id": "IS_NET_SALES",
        "tab": "Hist_IS",
        "order": 100,
        "label": "Net sales",
        "kind": "fact",
        "unit": "USD",
        "period_type": "duration",
        "status": "approved",
    },
    {
        "id": "IS_COGS",
        "tab": "Hist_IS",
        "order": 200,
        "label": "Cost of goods sold",
        "kind": "fact",
        "unit": "USD",
        "period_type": "duration",
        "status": "approved",
    },
    {
        "id": "IS_GROSS_PROFIT",
        "tab": "Hist_IS",
        "order": 300,
        "label": "Gross profit",
        "kind": "formula",
        "unit": "USD",
        "period_type": "duration",
        "template": "{IS_NET_SALES} - {IS_COGS}",
        "status": "approved",
    },
    {
        "id": "HDR_IS_SECTION",
        "tab": "Hist_IS",
        "order": 50,
        "label": "Revenue",
        "kind": "header",
        "status": "approved",
    },
    {
        "id": "AS_IN_SALES_GROWTH",
        "tab": "Assumptions",
        "order": 100,
        "label": "Sales growth input",
        "kind": "fact",
        "unit": "ratio",
        "status": "approved",
        "owner": "human",
    },
]

PERIOD_ENDS = [dt.date(2023, 12, 31), dt.date(2024, 12, 29), dt.date(2025, 12, 28)]


def _curated():
    rows = [
        {"line_id": "IS_NET_SALES", "statement": "IS", "period_start": dt.date(2023, 1, 2), "period_end": PERIOD_ENDS[0], "value": 4301200000.0},
        {"line_id": "IS_NET_SALES", "statement": "IS", "period_start": dt.date(2024, 1, 1), "period_end": PERIOD_ENDS[1], "value": 4540600000.0},
        {"line_id": "IS_NET_SALES", "statement": "IS", "period_start": dt.date(2024, 12, 30), "period_end": PERIOD_ENDS[2], "value": 4704800000.0},
        {"line_id": "IS_COGS", "statement": "IS", "period_start": dt.date(2023, 1, 2), "period_end": PERIOD_ENDS[0], "value": 2810000000.0},
        {"line_id": "IS_COGS", "statement": "IS", "period_start": dt.date(2024, 1, 1), "period_end": PERIOD_ENDS[1], "value": 2980500000.0},
        {"line_id": "IS_COGS", "statement": "IS", "period_start": dt.date(2024, 12, 30), "period_end": PERIOD_ENDS[2], "value": 3069600000.0},
    ]
    return pd.DataFrame(rows)


def _layouts():
    return {"Hist_IS": TabLayout("Hist_IS", LINE_ITEMS), "Assumptions": TabLayout("Assumptions", LINE_ITEMS)}


def test_build_write_set_scales_facts_and_resolves_formulas():
    layouts = _layouts()
    period_columns = build_period_columns(PERIOD_ENDS, include_opening=False)
    write_set = build_write_set(
        "Hist_IS", LINE_ITEMS, _curated(), layouts, period_columns, ["F", "G", "H"], FAKE_SETTINGS
    )

    sales_row = layouts["Hist_IS"].row_of("IS_NET_SALES")
    assert write_set[("Hist_IS", f"F{sales_row}")]["value_or_formula"] == 4301.2  # scaled to $ millions
    assert write_set[("Hist_IS", f"F{sales_row}")]["kind"] == "fact"

    gp_row = layouts["Hist_IS"].row_of("IS_GROSS_PROFIT")
    formula_cell = write_set[("Hist_IS", f"F{gp_row}")]
    assert formula_cell["value_or_formula"] == f"=F{sales_row} - F{layouts['Hist_IS'].row_of('IS_COGS')}"
    assert formula_cell["kind"] == "same_tab_formula"


def test_opening_column_excluded_for_duration_only_tab():
    """Regression test: column E (opening-balance-sheet-only) must never be used
    on a duration-only tab like Hist_IS, even when include_opening_balance_sheet
    is on globally and period_columns includes E. First live run wrote spurious
    "=E7-E8"-style formulas against blank E cells (silently read as 0) because
    period_columns was iterated unfiltered per tab."""
    layouts = _layouts()
    opening = dt.date(2023, 1, 1)
    period_columns = build_period_columns([opening, *PERIOD_ENDS], include_opening=True)
    assert period_columns[opening] == "E"

    write_set = build_write_set(
        "Hist_IS", LINE_ITEMS, _curated(), layouts, period_columns, ["E", "F", "G", "H"], FAKE_SETTINGS
    )

    assert not any(a1.startswith("E") for (tab, a1) in write_set if tab == "Hist_IS")


def test_header_row_has_no_data_cells():
    layouts = _layouts()
    period_columns = build_period_columns(PERIOD_ENDS, include_opening=False)
    write_set = build_write_set(
        "Hist_IS", LINE_ITEMS, _curated(), layouts, period_columns, ["F", "G", "H"], FAKE_SETTINGS
    )
    hdr_row = layouts["Hist_IS"].row_of("HDR_IS_SECTION")
    for col in ("F", "G", "H"):
        assert ("Hist_IS", f"{col}{hdr_row}") not in write_set


def test_human_owned_cells_excluded_from_write_set():
    layouts = _layouts()
    period_columns = build_period_columns(PERIOD_ENDS, include_opening=False)
    write_set = build_write_set(
        "Assumptions", LINE_ITEMS, _curated(), layouts, period_columns, ["F", "G", "H"], FAKE_SETTINGS
    )
    human_row = layouts["Assumptions"].row_of("AS_IN_SALES_GROWTH")
    for col in ("F", "G", "H"):
        assert ("Assumptions", f"{col}{human_row}") not in write_set
    # and nothing on that tab is marked human_input either, since it's excluded outright
    assert not any(cell["kind"] == "human_input" for cell in write_set.values())


def test_dry_run_deterministic(tmp_path):
    layouts = _layouts()
    period_columns = build_period_columns(PERIOD_ENDS, include_opening=False)
    write_set = build_write_set(
        "Hist_IS", LINE_ITEMS, _curated(), layouts, period_columns, ["F", "G", "H"], FAKE_SETTINGS
    )
    path1 = write_dry_run(write_set, tmp_path / "run1")
    path2 = write_dry_run(write_set, tmp_path / "run2")
    assert path1.read_text(encoding="utf-8") == path2.read_text(encoding="utf-8")
