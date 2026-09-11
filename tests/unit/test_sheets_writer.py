import datetime as dt
from types import SimpleNamespace

import pandas as pd

from site3sm.layout import TabLayout, build_period_columns
from site3sm.sheets_writer import (
    _col_to_index,
    _index_to_col,
    build_readme_write_set,
    build_sources_write_set,
    build_write_set,
    write_dry_run,
)

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


def test_fact_then_formula_first_column_fact_rest_formula():
    """Hist_CF's beginning-cash line (spec Sec 10.1): a fact for the earliest
    historical column, a formula linking to the prior column's ending cash for
    every column after that."""
    cf_items = [
        {
            "id": "CF_ENDING",
            "tab": "Hist_CF",
            "order": 100,
            "label": "Ending",
            "kind": "fact",
            "unit": "USD",
            "period_type": "instant",
            "status": "approved",
        },
        {
            "id": "CF_BEGINNING",
            "tab": "Hist_CF",
            "order": 90,
            "label": "Beginning",
            "kind": "fact_then_formula",
            "unit": "USD",
            "period_type": "instant",
            "template": "{CF_ENDING@prev}",
            "status": "approved",
        },
    ]
    layouts = {"Hist_CF": TabLayout("Hist_CF", cf_items)}
    # CF_BEGINNING and CF_ENDING are the same underlying instant fact reused across
    # two presentation rows, selected against the FULL instant axis including the
    # opening date -- exercise that here (even though Hist_CF never renders column
    # E itself) so the "look up the prior instant, not this column's own date"
    # fix is actually tested, not accidentally trivial.
    opening = dt.date(2023, 1, 1)
    period_columns = build_period_columns([opening, *PERIOD_ENDS], include_opening=True)
    curated = pd.DataFrame(
        [
            # deliberately DIFFERENT values at the opening date vs. F's own date,
            # since CF_BEGINNING and CF_ENDING share the same concept -- if the
            # lookup regresses to using F's own date, this test will catch it
            {"line_id": "CF_BEGINNING", "statement": "CF", "period_start": None, "period_end": opening, "value": 50000000.0},
            {"line_id": "CF_BEGINNING", "statement": "CF", "period_start": None, "period_end": PERIOD_ENDS[0], "value": 82500000.0},
        ]
    )

    write_set = build_write_set("Hist_CF", cf_items, curated, layouts, period_columns, ["E", "F", "G", "H"], FAKE_SETTINGS)

    begin_row = layouts["Hist_CF"].row_of("CF_BEGINNING")
    end_row = layouts["Hist_CF"].row_of("CF_ENDING")

    assert ("Hist_CF", f"E{begin_row}") not in write_set  # CF never uses column E

    f_cell = write_set[("Hist_CF", f"F{begin_row}")]
    assert f_cell["kind"] == "fact"
    assert f_cell["value_or_formula"] == 50.0  # the OPENING instant's value, not F's own date's value (82.5)

    g_cell = write_set[("Hist_CF", f"G{begin_row}")]
    assert g_cell["kind"] == "same_tab_formula"
    assert g_cell["value_or_formula"] == f"=F{end_row}"

    h_cell = write_set[("Hist_CF", f"H{begin_row}")]
    assert h_cell["value_or_formula"] == f"=G{end_row}"


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


def test_col_index_roundtrip():
    for col in ["A", "H", "O", "Z", "AA", "AZ", "BA"]:
        assert _index_to_col(_col_to_index(col)) == col


def test_build_sources_write_set_one_row_per_curated_fact():
    line_items = [{"id": "IS_NET_SALES", "label": "Net sales"}]
    curated = pd.DataFrame(
        [
            {
                "line_id": "IS_NET_SALES",
                "statement": "IS",
                "period_end": dt.date(2025, 12, 28),
                "value": 4704800000.0,
                "unit": "USD",
                "concept": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                "dimensions": "{}",
                "accn": "0001650729-26-000005",
                "form": "10-K",
                "filed": dt.date(2026, 2, 19),
                "source": "companyfacts",
                "recast_flag": False,
                "raw_path": "data/raw/fake.body",
            }
        ]
    )
    write_set = build_sources_write_set(curated, line_items)

    assert write_set[("Sources", "A1")]["value_or_formula"] == "line_id"  # header row
    assert write_set[("Sources", "A2")]["value_or_formula"] == "IS_NET_SALES"
    assert write_set[("Sources", "B2")]["value_or_formula"] == "Hist_IS"
    assert write_set[("Sources", "C2")]["value_or_formula"] == "Net sales"  # label joined from line_items
    assert write_set[("Sources", "E2")]["value_or_formula"] == 4704800000.0  # unscaled, per spec Sec 10.4
    assert write_set[("Sources", "H2")]["value_or_formula"] == "standard"  # derived taxonomy_type


def test_build_readme_write_set_includes_run_id_and_target():
    write_set = build_readme_write_set("20260910_120000", "scratch")
    texts = [cell["value_or_formula"] for cell in write_set.values()]
    assert any("20260910_120000" in t for t in texts)
    assert any("scratch" in t for t in texts)
