import pytest

from site3sm.checks import build_checks_layout, build_checks_write_set
from site3sm.layout import LayoutError, TabLayout

ACTIVE_LETTERS = ["E", "F", "G", "H"]

# Every line_id referenced by checks.py's IDENTITY_CHECKS/CF_CASH_CONTINUITY templates.
BS_LINES = ["BS_TOTAL_ASSETS", "BS_TOTAL_LIABILITIES", "BS_REDEEMABLE_NON_CONTROLLING_INTEREST", "BS_TOTAL_STOCKHOLDERS_EQUITY"]
CF_LINES = [
    "CF_NET_CASH_PROVIDED_BY_OPERATING_ACTIVITIES",
    "CF_NET_CASH_USED_IN_INVESTING_ACTIVITIES",
    "CF_NET_CASH_USED_IN_FINANCING_ACTIVITIES",
    "CF_EFFECT_OF_EXCHANGE_RATE_ON_CASH",
    "CF_NET_CHANGE_IN_CASH",
    "CF_BEGINNING",
    "CF_ENDING",
    "CF_NET_INCOME",
]
IS_LINES = ["IS_NET_INCOME"]
EQ_LINES = [
    "EQ_STOCKHOLDERSEQUITY_RETAINED_EARNINGS_RETAINED_EARNINGS_BEGIN",
    "EQ_NETINCOMELOSS_RETAINED_EARNINGS_RETAINED_EARNINGS",
    "EQ_STOCKHOLDERSEQUITY_RETAINED_EARNINGS_RETAINED_EARNINGS_ENDIN",
    "EQ_REDEEMABLE_CONVERTIBLE_PREFERRED_STOCK_BEGINNING_BALANCE",
    "EQ_NET_INCOME_ATTRIBUTABLE_TO_NON_CONTROLLING_INTEREST",
    "EQ_ACQUISITION_OF_NON_CONTROLLING_INTEREST",
    "EQ_ADJUSTMENT_OF_NON_CONTROLLING_INTEREST_TO_REDEMPTION_VALUE",
    "EQ_DISTRIBUTIONS_TO_NON_CONTROLLING_INTEREST",
    "EQ_REDEEMABLE_CONVERTIBLE_PREFERRED_STOCK_ENDING_BALANCE",
]


def _fake_line_items(tab, ids):
    return [{"id": lid, "tab": tab, "order": i * 10, "status": "approved", "kind": "fact"} for i, lid in enumerate(ids)]


def _full_layouts(cash_tie_configured=False):
    layouts = {
        "Hist_BS": TabLayout("Hist_BS", _fake_line_items("Hist_BS", BS_LINES)),
        "Hist_CF": TabLayout("Hist_CF", _fake_line_items("Hist_CF", CF_LINES)),
        "Hist_IS": TabLayout("Hist_IS", _fake_line_items("Hist_IS", IS_LINES)),
        "Hist_EQ": TabLayout("Hist_EQ", _fake_line_items("Hist_EQ", EQ_LINES)),
    }
    layouts["Checks"] = build_checks_layout(cash_tie_configured)
    return layouts


def test_checks_layout_has_no_row_collisions():
    layout = build_checks_layout(cash_tie_configured=True)
    assert len(set(layout.row_map.values())) == len(layout.row_map)
    assert layout.kind_of("CHK_TOLERANCE") == "scalar"
    assert layout.kind_of("CHK_MASTER") == "scalar"
    assert layout.kind_of("CHK_BS_BALANCE") == "check_diff"
    assert layout.kind_of("CHK_BS_BALANCE_STATUS") == "check_status"


def test_tolerance_written_once_at_fixed_column():
    layouts = _full_layouts()
    write_set = build_checks_write_set(layouts, ACTIVE_LETTERS, cash_tie=None, tolerance_scaled=0.1)
    tol_row = layouts["Checks"].row_of("CHK_TOLERANCE")
    assert write_set[("Checks", f"F{tol_row}")]["value_or_formula"] == 0.1
    # not written at any other column
    for col in ("E", "G", "H"):
        assert ("Checks", f"{col}{tol_row}") not in write_set


def test_bs_balance_written_at_all_four_columns_including_opening():
    layouts = _full_layouts()
    write_set = build_checks_write_set(layouts, ACTIVE_LETTERS, cash_tie=None, tolerance_scaled=0.1)
    diff_row = layouts["Checks"].row_of("CHK_BS_BALANCE")
    for col in ACTIVE_LETTERS:
        cell = write_set[("Checks", f"{col}{diff_row}")]
        assert cell["kind"] == "cross_tab_formula"
        assert cell["value_or_formula"].startswith("=")
        assert "Hist_BS" in cell["value_or_formula"]


def test_cf_net_change_never_written_at_opening_column():
    layouts = _full_layouts()
    write_set = build_checks_write_set(layouts, ACTIVE_LETTERS, cash_tie=None, tolerance_scaled=0.1)
    diff_row = layouts["Checks"].row_of("CHK_CF_NET_CHANGE")
    assert ("Checks", f"E{diff_row}") not in write_set
    for col in ("F", "G", "H"):
        assert ("Checks", f"{col}{diff_row}") in write_set


def test_cf_cash_continuity_blank_at_earliest_column_only():
    """CHK_CF_CASH_CONTINUITY's @prev lands on Hist_CF, which never has data at
    column E (CF is not opening-eligible) -- F is genuinely not computable and
    must render blank, not a formula against an empty cell."""
    layouts = _full_layouts()
    write_set = build_checks_write_set(layouts, ACTIVE_LETTERS, cash_tie=None, tolerance_scaled=0.1)
    diff_row = layouts["Checks"].row_of("CHK_CF_CASH_CONTINUITY")
    status_row = layouts["Checks"].row_of("CHK_CF_CASH_CONTINUITY_STATUS")
    assert ("Checks", f"F{diff_row}") not in write_set
    assert ("Checks", f"F{status_row}") not in write_set
    assert ("Checks", f"G{diff_row}") in write_set
    assert ("Checks", f"H{diff_row}") in write_set


def test_status_formula_references_tolerance_at_fixed_column_from_every_period():
    layouts = _full_layouts()
    write_set = build_checks_write_set(layouts, ACTIVE_LETTERS, cash_tie=None, tolerance_scaled=0.1)
    tol_row = layouts["Checks"].row_of("CHK_TOLERANCE")
    status_row = layouts["Checks"].row_of("CHK_BS_BALANCE_STATUS")
    for col in ACTIVE_LETTERS:
        formula = write_set[("Checks", f"{col}{status_row}")]["value_or_formula"]
        assert f"'Checks'!$F${tol_row}" in formula  # always column F, regardless of calling column


def test_cash_tie_omitted_when_not_configured():
    layouts = _full_layouts(cash_tie_configured=False)
    write_set = build_checks_write_set(layouts, ACTIVE_LETTERS, cash_tie=None, tolerance_scaled=0.1)
    assert not any(a1_tab[0] == "Checks" and "CHK_CASH_TIE" in str(cell) for a1_tab, cell in write_set.items())
    assert "CHK_CASH_TIE" not in layouts["Checks"].row_map


def test_cash_tie_written_when_configured():
    layouts = _full_layouts(cash_tie_configured=True)
    write_set = build_checks_write_set(
        layouts, ACTIVE_LETTERS, cash_tie=("CF_ENDING", "BS_TOTAL_ASSETS"), tolerance_scaled=0.1
    )
    diff_row = layouts["Checks"].row_of("CHK_CASH_TIE")
    assert ("Checks", f"F{diff_row}") in write_set


def test_chk_master_countif_covers_every_status_row():
    layouts = _full_layouts()
    write_set = build_checks_write_set(layouts, ACTIVE_LETTERS, cash_tie=None, tolerance_scaled=0.1)
    master_row = layouts["Checks"].row_of("CHK_MASTER")
    formula = write_set[("Checks", f"F{master_row}")]["value_or_formula"]
    assert formula.startswith("=IF((")
    assert "COUNTIF" in formula
    status_rows = [r for lid, r in layouts["Checks"].row_map.items() if layouts["Checks"].kind_of(lid) == "check_status"]
    for r in status_rows:
        assert f"E{r}:H{r}" in formula


def test_no_subtotal_checks_rendered():
    """CHK_SUBTOTAL_<LINE_ID> is deliberately not part of the Checks tab -- see
    checks.py's module docstring for why (single-entry rule makes it tautological)."""
    layout = build_checks_layout(cash_tie_configured=True)
    assert not any(lid.startswith("CHK_SUBTOTAL_") for lid in layout.row_map)
