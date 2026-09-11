import pytest

from site3sm.formulas import cell_kind, resolve_formula
from site3sm.layout import LayoutError, TabLayout

IS_ITEMS = [
    {"id": "IS_NET_SALES", "tab": "Hist_IS", "order": 100, "status": "approved", "kind": "fact"},
    {"id": "IS_COGS", "tab": "Hist_IS", "order": 200, "status": "approved", "kind": "fact"},
    {"id": "IS_GROSS_PROFIT", "tab": "Hist_IS", "order": 300, "status": "approved", "kind": "formula"},
]
BS_ITEMS = [
    {"id": "BS_CASH", "tab": "Hist_BS", "order": 100, "status": "approved", "kind": "fact"},
]
CHECKS_ITEMS = [
    {"id": "CHK_TOLERANCE", "tab": "Checks", "order": 100, "status": "approved", "kind": "scalar"},
]


@pytest.fixture
def layouts():
    return {
        "Hist_IS": TabLayout("Hist_IS", IS_ITEMS),
        "Hist_BS": TabLayout("Hist_BS", BS_ITEMS),
        "Checks": TabLayout("Checks", CHECKS_ITEMS),
    }


LETTERS = ["E", "F", "G", "H"]


def test_same_tab_token(layouts):
    formula = resolve_formula("{IS_NET_SALES} - {IS_COGS}", "Hist_IS", "G", layouts, LETTERS)
    assert formula == f"=G{layouts['Hist_IS'].row_of('IS_NET_SALES')} - G{layouts['Hist_IS'].row_of('IS_COGS')}"


def test_cross_tab_token(layouts):
    formula = resolve_formula("{Hist_BS!BS_CASH}", "Hist_IS", "G", layouts, LETTERS)
    row = layouts["Hist_BS"].row_of("BS_CASH")
    assert formula == f"='Hist_BS'!G{row}"


def test_prev_token_same_tab(layouts):
    formula = resolve_formula("{IS_NET_SALES@prev}", "Hist_IS", "H", layouts, LETTERS)
    row = layouts["Hist_IS"].row_of("IS_NET_SALES")
    assert formula == f"=G{row}"


def test_prev_token_cross_tab(layouts):
    formula = resolve_formula("{Hist_BS!BS_CASH@prev}", "Hist_IS", "H", layouts, LETTERS)
    row = layouts["Hist_BS"].row_of("BS_CASH")
    assert formula == f"='Hist_BS'!G{row}"


def test_abs_token_on_scalar(layouts):
    formula = resolve_formula("{Checks!CHK_TOLERANCE@abs}", "Hist_IS", "G", layouts, LETTERS)
    row = layouts["Checks"].row_of("CHK_TOLERANCE")
    assert formula == f"='Checks'!$G${row}"


def test_multi_token_template(layouts):
    formula = resolve_formula(
        "{IS_NET_SALES} - {IS_COGS} + {Hist_BS!BS_CASH}", "Hist_IS", "F", layouts, LETTERS
    )
    assert formula.startswith("=")
    assert formula.count("!") == 1  # only the cross-tab ref is sheet-qualified


def test_unknown_line_id_raises(layouts):
    with pytest.raises(LayoutError):
        resolve_formula("{IS_NOPE}", "Hist_IS", "G", layouts, LETTERS)


def test_unknown_tab_raises(layouts):
    with pytest.raises(LayoutError):
        resolve_formula("{Hist_ZZZ!SOMETHING}", "Hist_IS", "G", layouts, LETTERS)


def test_prev_with_no_prior_period_raises(layouts):
    with pytest.raises(LayoutError):
        resolve_formula("{IS_NET_SALES@prev}", "Hist_IS", "E", layouts, LETTERS)


def test_abs_on_non_scalar_raises(layouts):
    with pytest.raises(LayoutError):
        resolve_formula("{IS_NET_SALES@abs}", "Hist_IS", "G", layouts, LETTERS)


def test_unresolved_brace_raises(layouts):
    # A template with a stray/malformed brace the token regex can't match at all
    # should still be caught rather than silently written to the sheet.
    with pytest.raises(LayoutError):
        resolve_formula("{IS_NET_SALES} + {", "Hist_IS", "G", layouts, LETTERS)


def test_cell_kind_same_vs_cross_tab():
    assert cell_kind("{IS_NET_SALES} - {IS_COGS}", "Hist_IS") == "same_tab"
    assert cell_kind("{Hist_BS!BS_CASH}", "Hist_IS") == "cross_tab"
    assert cell_kind("{IS_NET_SALES} + {Hist_BS!BS_CASH}", "Hist_IS") == "cross_tab"


def test_collision_guard_reproduces_legacy_offset_pattern():
    """The legacy bug (R11): a prior script wrote a value into the wrong column,
    effectively making two logically different (tab, line, period) cells collide on
    one physical address. Reproduce that shape at the layout level -- two line_items
    resolving to the same id -- and confirm it raises instead of silently
    overwriting one with the other."""
    colliding = [
        {"id": "IS_NET_SALES", "tab": "Hist_IS", "order": 100, "status": "approved", "kind": "fact"},
        {"id": "IS_NET_SALES", "tab": "Hist_IS", "order": 999, "status": "approved", "kind": "fact"},
    ]
    with pytest.raises(LayoutError):
        TabLayout("Hist_IS", colliding)
