"""Builds the Checks tab (spec Sec 10.2): CHK_TOLERANCE scalar, a difference-row
plus status-row pair per identity check, and CHK_MASTER. Every check is a live
Sheets formula -- nothing here is a value Python computed and pasted in (R5, R6).

CHK_SUBTOTAL_<LINE_ID> is deliberately NOT rendered here. Under the single-entry
rule (spec Sec 10.1), a formula-kind line's cell IS the only place its value
exists on the sheet -- there is no separately-written "reported" value anywhere
to diff it against live (that comparison already happened once, rigorously, in
Python during Phase 3's validate.py, against the actual curated/reported value,
and is recorded in reports/validation_*.md). A "check" that compares a formula
cell to itself would be tautological, not a check.
"""
from __future__ import annotations

from site3sm.formulas import cell_kind, resolve_formula
from site3sm.layout import LABEL_COL, SCALAR_COL, LayoutError, TabLayout

TAB = "Checks"

# (id, description, template, needs_opening_column)
# needs_opening_column controls which period columns this check is written for:
# True -> the tab's full active axis (E,F,G,H on this build); False -> F,G,H only.
IDENTITY_CHECKS = [
    (
        "CHK_BS_BALANCE",
        "Total assets minus (total liabilities + RNCI + total stockholders' equity)",
        "{Hist_BS!BS_TOTAL_ASSETS} - ({Hist_BS!BS_TOTAL_LIABILITIES} + {Hist_BS!BS_REDEEMABLE_NON_CONTROLLING_INTEREST} + {Hist_BS!BS_TOTAL_STOCKHOLDERS_EQUITY})",
        True,
    ),
    (
        "CHK_CF_NET_CHANGE",
        "Sum of CF section totals and reconciling lines minus net change in cash",
        "{Hist_CF!CF_NET_CASH_PROVIDED_BY_OPERATING_ACTIVITIES} + {Hist_CF!CF_NET_CASH_USED_IN_INVESTING_ACTIVITIES} + {Hist_CF!CF_NET_CASH_USED_IN_FINANCING_ACTIVITIES} + {Hist_CF!CF_EFFECT_OF_EXCHANGE_RATE_ON_CASH} - {Hist_CF!CF_NET_CHANGE_IN_CASH}",
        False,
    ),
    (
        "CHK_CF_CASH_ROLL",
        "Beginning cash + net change minus ending cash",
        "{Hist_CF!CF_BEGINNING} + {Hist_CF!CF_NET_CHANGE_IN_CASH} - {Hist_CF!CF_ENDING}",
        False,
    ),
    (
        "CHK_NI_TIE",
        "IS consolidated net income minus CF starting net income",
        "{Hist_IS!IS_NET_INCOME} - {Hist_CF!CF_NET_INCOME}",
        False,
    ),
    (
        "CHK_RE_ROLLFORWARD",
        "Prior retained earnings + net income minus current retained earnings",
        "{Hist_EQ!EQ_STOCKHOLDERSEQUITY_RETAINED_EARNINGS_RETAINED_EARNINGS_BEGIN@prev} + {Hist_EQ!EQ_NETINCOMELOSS_RETAINED_EARNINGS_RETAINED_EARNINGS} - {Hist_EQ!EQ_STOCKHOLDERSEQUITY_RETAINED_EARNINGS_RETAINED_EARNINGS_ENDIN}",
        False,
    ),
    (
        "CHK_RNCI_ROLLFORWARD",
        "Prior RNCI + approved EQ components minus current RNCI",
        "{Hist_EQ!EQ_REDEEMABLE_CONVERTIBLE_PREFERRED_STOCK_BEGINNING_BALANCE@prev} + {Hist_EQ!EQ_NET_INCOME_ATTRIBUTABLE_TO_NON_CONTROLLING_INTEREST} + {Hist_EQ!EQ_ACQUISITION_OF_NON_CONTROLLING_INTEREST} + {Hist_EQ!EQ_ADJUSTMENT_OF_NON_CONTROLLING_INTEREST_TO_REDEMPTION_VALUE} - {Hist_EQ!EQ_DISTRIBUTIONS_TO_NON_CONTROLLING_INTEREST} - {Hist_EQ!EQ_REDEEMABLE_CONVERTIBLE_PREFERRED_STOCK_ENDING_BALANCE}",
        False,
    ),
]

# CHK_CF_CASH_CONTINUITY is a genuine exception to the "F,G,H" default above: its
# @prev reference is onto Hist_CF's OWN CF_ENDING, but Hist_CF is not an
# opening-column-eligible tab (spec Sec 9.6 -- E is "BS/EQ only"), so there is no
# real data at column E to link F's "prior" to. Column F for this check is
# genuinely not computable (spec Sec 10.2: "blank periods render blank, not OK"),
# so it's written for G/H only, never F -- unlike CHK_RE_ROLLFORWARD/
# CHK_RNCI_ROLLFORWARD, whose @prev lands on Hist_EQ, which IS opening-eligible
# and has real data at E.
CF_CASH_CONTINUITY = (
    "CHK_CF_CASH_CONTINUITY",
    "CF beginning cash(t) minus CF ending cash(t-1)",
    "{Hist_CF!CF_BEGINNING} - {Hist_CF!CF_ENDING@prev}",
)


def build_checks_layout(cash_tie_configured: bool) -> TabLayout:
    """Synthetic line_items for the Checks tab -- not statement facts, so they
    don't belong in config/line_items.yaml, but TabLayout's row-assignment logic
    (unique sequential rows, no collisions) applies identically."""
    entries = [{"id": "CHK_TOLERANCE", "tab": TAB, "order": 10, "status": "approved", "kind": "scalar"}]
    order = 20
    for check_id, _desc, _template, _needs_opening in IDENTITY_CHECKS:
        entries.append({"id": check_id, "tab": TAB, "order": order, "status": "approved", "kind": "check_diff"})
        entries.append({"id": f"{check_id}_STATUS", "tab": TAB, "order": order + 1, "status": "approved", "kind": "check_status"})
        order += 10
    check_id, _desc, _template = CF_CASH_CONTINUITY
    entries.append({"id": check_id, "tab": TAB, "order": order, "status": "approved", "kind": "check_diff"})
    entries.append({"id": f"{check_id}_STATUS", "tab": TAB, "order": order + 1, "status": "approved", "kind": "check_status"})
    order += 10
    if cash_tie_configured:
        entries.append({"id": "CHK_CASH_TIE", "tab": TAB, "order": order, "status": "approved", "kind": "check_diff"})
        entries.append({"id": "CHK_CASH_TIE_STATUS", "tab": TAB, "order": order + 1, "status": "approved", "kind": "check_status"})
        order += 10
    entries.append({"id": "CHK_MASTER", "tab": TAB, "order": order, "status": "approved", "kind": "scalar"})
    return TabLayout(TAB, entries)


def build_checks_write_set(
    layouts: dict[str, TabLayout],
    active_letters: list[str],
    cash_tie: tuple[str, str] | None,
    tolerance_scaled: float,
) -> dict[tuple[str, str], dict]:
    checks_layout = layouts[TAB]
    write_set: dict[tuple[str, str], dict] = {}

    def put(a1: str, kind: str, value_or_formula) -> None:
        key = (TAB, a1)
        if key in write_set:
            raise LayoutError(f"collision: {TAB}!{a1} already targeted by another cell")
        write_set[key] = {"kind": kind, "value_or_formula": value_or_formula, "owner": "code"}

    # Checks intentionally skips the period-header-row block every Hist_* tab
    # carries: those rows restate period_start/period_end from curated data, which
    # Checks doesn't own or have an independent copy of, and every check's own
    # label already makes clear what it's testing without needing to re-derive
    # fiscal year labels here too. The period *columns themselves* still line up
    # 1:1 with Hist_*'s via the shared period_columns map -- only the redundant
    # label restatement is skipped.

    tol_row = checks_layout.row_of("CHK_TOLERANCE")
    put(f"{LABEL_COL}{tol_row}", "header", "CHK_TOLERANCE")
    put(f"{SCALAR_COL}{tol_row}", "fact", tolerance_scaled)

    def write_check(check_id: str, description: str, template: str, target_cols: list[str]) -> None:
        diff_row = checks_layout.row_of(check_id)
        status_row = checks_layout.row_of(f"{check_id}_STATUS")
        put(f"{LABEL_COL}{diff_row}", "header", description)
        for col in target_cols:
            try:
                formula = resolve_formula(template, TAB, col, layouts, active_letters)
            except LayoutError:
                # Belt-and-suspenders, not the primary mechanism: the resolver only
                # catches @prev with literally no earlier column at all (e.g. @prev
                # at column E itself). It can't tell "column exists but this tab
                # never writes real data there" -- that's a per-check judgment
                # (see CF_CASH_CONTINUITY above), expressed by callers passing a
                # deliberately narrower target_cols, not by relying on this catch.
                continue
            fkind = "cross_tab_formula" if cell_kind(template, TAB) == "cross_tab" else "same_tab_formula"
            put(f"{col}{diff_row}", fkind, formula)

            status_template = f'IF(ABS({{{check_id}}})<={{Checks!CHK_TOLERANCE@abs}},"OK","CHECK")'
            status_formula = resolve_formula(status_template, TAB, col, layouts, active_letters)
            put(f"{col}{status_row}", "same_tab_formula", status_formula)

    full_cols = [c for c in active_letters]
    duration_cols = [c for c in active_letters if c != "E"]

    for check_id, description, template, needs_opening in IDENTITY_CHECKS:
        write_check(check_id, description, template, full_cols if needs_opening else duration_cols)

    check_id, description, template = CF_CASH_CONTINUITY
    # F excluded explicitly, not via the resolver's own error handling: @prev(F)
    # resolves to column E *syntactically* just fine (E is a real column in the
    # active-letters list), but Hist_CF never writes real data there (CF is not
    # opening-eligible) -- the resolver has no way to know that, so it can't be
    # relied on to catch this case on its own. See module docstring.
    write_check(check_id, description, template, duration_cols[1:])

    if cash_tie is not None:
        cf_line, bs_line = cash_tie
        write_check(
            "CHK_CASH_TIE",
            "CF ending cash minus BS cash",
            f"{{Hist_CF!{cf_line}}} - {{Hist_BS!{bs_line}}}",
            full_cols,
        )

    # CHK_MASTER: OK only if zero status cells anywhere on this tab read "CHECK"
    status_rows = [r for lid, r in checks_layout.row_map.items() if checks_layout.kind_of(lid) == "check_status"]
    master_row = checks_layout.row_of("CHK_MASTER")
    put(f"{LABEL_COL}{master_row}", "header", "CHK_MASTER")
    if status_rows:
        first_col, last_col = active_letters[0], active_letters[-1]
        ranges = [f"{first_col}{r}:{last_col}{r}" for r in sorted(status_rows)]
        countif_terms = "+".join(f'COUNTIF({r},"CHECK")' for r in ranges)
        put(
            f"{SCALAR_COL}{master_row}",
            "same_tab_formula",
            f'=IF(({countif_terms})=0,"OK",CONCATENATE("FAILURES: ",({countif_terms})))',
        )

    return write_set
