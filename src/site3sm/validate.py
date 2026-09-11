"""Validate accounting identities on the curated frame, in Python, before anything
is written to a sheet (spec Sec 9.5). Nothing here writes a sheet -- rendering only
happens after every check in the report passes (spec Sec 9.5, 'Any failure exits
non-zero, and rendering does not run').

Line IDs below are resolved from the Gate-2-approved config/line_items.yaml for this
filing. If a future discover run changes IDs materially, these need re-pointing.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import re

import pandas as pd

TOKEN_RE = re.compile(r"\{([A-Za-z0-9_]+)\}")
SAFE_EXPR_RE = re.compile(r"^[0-9eE+\-*/(). \t]*$")

BS_TOTAL_ASSETS = "BS_TOTAL_ASSETS"
BS_TOTAL_LIABILITIES = "BS_TOTAL_LIABILITIES"
BS_RNCI = "BS_REDEEMABLE_NON_CONTROLLING_INTEREST"
BS_TOTAL_EQUITY = "BS_TOTAL_STOCKHOLDERS_EQUITY"
BS_CASH = "BS_CASH_AND_CASH_EQUIVALENTS"

CF_OPERATING = "CF_NET_CASH_PROVIDED_BY_OPERATING_ACTIVITIES"
CF_INVESTING = "CF_NET_CASH_USED_IN_INVESTING_ACTIVITIES"
CF_FINANCING = "CF_NET_CASH_USED_IN_FINANCING_ACTIVITIES"
CF_FX = "CF_EFFECT_OF_EXCHANGE_RATE_ON_CASH"
CF_NET_CHANGE = "CF_NET_CHANGE_IN_CASH"
CF_BEGINNING = "CF_BEGINNING"
CF_ENDING = "CF_ENDING"
CF_NET_INCOME = "CF_NET_INCOME"

IS_NET_INCOME = "IS_NET_INCOME"

EQ_RE_BEGIN = "EQ_STOCKHOLDERSEQUITY_RETAINED_EARNINGS_RETAINED_EARNINGS_BEGIN"
EQ_RE_NI = "EQ_NETINCOMELOSS_RETAINED_EARNINGS_RETAINED_EARNINGS"
EQ_RE_END = "EQ_STOCKHOLDERSEQUITY_RETAINED_EARNINGS_RETAINED_EARNINGS_ENDIN"

EQ_RNCI_BEGIN = "EQ_REDEEMABLE_CONVERTIBLE_PREFERRED_STOCK_BEGINNING_BALANCE"
EQ_RNCI_NI = "EQ_NET_INCOME_ATTRIBUTABLE_TO_NON_CONTROLLING_INTEREST"
EQ_RNCI_ACQUISITION = "EQ_ACQUISITION_OF_NON_CONTROLLING_INTEREST"
EQ_RNCI_ADJUSTMENT = "EQ_ADJUSTMENT_OF_NON_CONTROLLING_INTEREST_TO_REDEMPTION_VALUE"
EQ_RNCI_DISTRIBUTIONS = "EQ_DISTRIBUTIONS_TO_NON_CONTROLLING_INTEREST"
EQ_RNCI_END = "EQ_REDEEMABLE_CONVERTIBLE_PREFERRED_STOCK_ENDING_BALANCE"


def eval_template(template: str, values_by_line_id: dict[str, float]) -> float:
    def repl(m: re.Match) -> str:
        line_id = m.group(1)
        if line_id not in values_by_line_id:
            raise KeyError(line_id)
        return repr(float(values_by_line_id[line_id]))

    expr = TOKEN_RE.sub(repl, template)
    if not SAFE_EXPR_RE.match(expr):
        raise ValueError(f"template produced an unsafe expression: {expr!r}")
    return eval(expr, {"__builtins__": {}}, {})  # noqa: S307 - sanitized above


def _series(curated: pd.DataFrame, line_id: str) -> dict:
    sub = curated[curated["line_id"] == line_id]
    return dict(zip(sub["period_end"], sub["value"]))


def _mk(check_id: str, period, diff, tolerance: float | None, note: str = "") -> dict:
    if diff is None:
        return {"id": check_id, "period": period, "diff": None, "passed": False, "note": note or "not computable"}
    passed = abs(diff) <= tolerance if tolerance is not None else None
    return {"id": check_id, "period": period, "diff": diff, "passed": passed, "note": note}


def check_subtotals(curated: pd.DataFrame, line_items: list[dict], tolerance: float) -> list[dict]:
    allow_missing_ids = {li["id"] for li in line_items if li.get("allow_missing")}
    results = []
    for li in line_items:
        if li.get("status") != "approved" or li.get("kind") != "formula" or "template" not in li:
            continue
        tab_statement = li["tab"].replace("Hist_", "")
        tab_curated = curated[curated["statement"] == tab_statement]
        own = _series(curated, li["id"])
        template_children = set(TOKEN_RE.findall(li["template"]))
        for period_end in sorted(own):
            period_rows = tab_curated[tab_curated["period_end"] == period_end]
            values_by_line = dict(zip(period_rows["line_id"], period_rows["value"]))
            # a child line declared allow_missing (e.g. a text-only placeholder that
            # never carries a value) contributes 0 to the sum when genuinely absent,
            # rather than making the whole subtotal uncomputable
            for child_id in template_children - values_by_line.keys():
                if child_id in allow_missing_ids:
                    values_by_line[child_id] = 0.0
            check_id = f"CHK_SUBTOTAL_{li['id']}"
            try:
                computed = eval_template(li["template"], values_by_line)
            except KeyError as exc:
                results.append(_mk(check_id, period_end, None, tolerance, f"missing child value: {exc}"))
                continue
            reported = values_by_line.get(li["id"])
            results.append(_mk(check_id, period_end, computed - reported, tolerance))
    return results


def check_bs_balance(curated: pd.DataFrame, tolerance: float) -> list[dict]:
    assets, liabs, rnci, equity = (_series(curated, x) for x in (BS_TOTAL_ASSETS, BS_TOTAL_LIABILITIES, BS_RNCI, BS_TOTAL_EQUITY))
    results = []
    for period_end, ta in sorted(assets.items()):
        tl, se = liabs.get(period_end), equity.get(period_end)
        r = rnci.get(period_end, 0.0)  # RNCI may legitimately be absent/zero in earlier years
        diff = None if (tl is None or se is None) else ta - (tl + r + se)
        results.append(_mk("CHK_BS_BALANCE", period_end, diff, tolerance))
    return results


def check_cf_net_change(curated: pd.DataFrame, tolerance: float) -> list[dict]:
    op, inv, fin, fx, net = (_series(curated, x) for x in (CF_OPERATING, CF_INVESTING, CF_FINANCING, CF_FX, CF_NET_CHANGE))
    results = []
    for period_end, n in sorted(net.items()):
        parts = [op.get(period_end), inv.get(period_end), fin.get(period_end), fx.get(period_end, 0.0)]
        diff = None if any(p is None for p in parts[:3]) else sum(parts) - n
        results.append(_mk("CHK_CF_NET_CHANGE", period_end, diff, tolerance))
    return results


def _prior_instant_map(instant_ends: list) -> dict:
    s = sorted(instant_ends)
    return {end: (s[i - 1] if i > 0 else None) for i, end in enumerate(s)}


def check_cf_cash_roll(curated: pd.DataFrame, instant_ends: list, tolerance: float) -> list[dict]:
    ending = _series(curated, CF_ENDING)
    net = _series(curated, CF_NET_CHANGE)
    prior = _prior_instant_map(instant_ends)
    results = []
    for period_end, n in sorted(net.items()):
        prior_instant = prior.get(period_end)
        begin = ending.get(prior_instant) if prior_instant else None
        end = ending.get(period_end)
        diff = None if (begin is None or end is None) else (begin + n) - end
        results.append(_mk("CHK_CF_CASH_ROLL", period_end, diff, tolerance))
    return results


def check_cf_cash_continuity(curated: pd.DataFrame, instant_ends: list, tolerance: float) -> list[dict]:
    """CF beginning(t) vs CF ending(t-1).

    NOTE on the lookup: CF_BEGINNING and CF_ENDING are both instant-type facts on the
    *same* concept, selected against the same set of instant dates -- there is no
    separate "beginning of FYt" fact distinct from "cash at the prior instant". So
    both series are keyed by *instant date*, not fiscal year: CF_BEGINNING's value
    under key `e_t` means "cash at instant e_t", not "cash at the start of the year
    ending e_t". Looking either series up at the *prior* instant (not at the current
    duration's own end) is what actually answers "beginning of FYt". Getting this
    backwards (indexing by e_t directly) previously produced a diff equal to the
    whole year's net change in cash instead of ~zero.

    Given both series are selected identically today (single anchor filing), this
    check is close to tautological -- it would become a genuine cross-filing check
    once select_facts.py sources beginning/ending cash from each fiscal year's own
    filing rather than one anchor's restated columns.
    """
    begin = _series(curated, CF_BEGINNING)
    ending = _series(curated, CF_ENDING)
    prior = _prior_instant_map(instant_ends)
    results = []
    for period_end in sorted(prior):
        prior_instant = prior[period_end]
        if prior_instant is None:
            continue
        b, prior_ending = begin.get(prior_instant), ending.get(prior_instant)
        diff = None if (b is None or prior_ending is None) else b - prior_ending
        results.append(_mk("CHK_CF_CASH_CONTINUITY", period_end, diff, tolerance))
    return results


def check_cash_tie(curated: pd.DataFrame, settings, tolerance: float) -> list[dict]:
    cf_line = settings.cash_tie.cf_ending_cash_line_id
    bs_line = settings.cash_tie.bs_cash_line_id
    if not cf_line or not bs_line:
        return [_mk("CHK_CASH_TIE", None, None, tolerance, "not configured -- settings.cash_tie.* still null (PLAN.md Sec 5 item 5)")]
    cf = _series(curated, cf_line)
    bs = _series(curated, bs_line)
    results = []
    for period_end, cf_val in sorted(cf.items()):
        bs_val = bs.get(period_end)
        diff = None if bs_val is None else cf_val - bs_val
        results.append(_mk("CHK_CASH_TIE", period_end, diff, tolerance))
    return results


def check_ni_tie(curated: pd.DataFrame, tolerance: float) -> list[dict]:
    is_ni, cf_ni = _series(curated, IS_NET_INCOME), _series(curated, CF_NET_INCOME)
    results = []
    for period_end, v in sorted(is_ni.items()):
        cf_v = cf_ni.get(period_end)
        diff = None if cf_v is None else v - cf_v
        results.append(_mk("CHK_NI_TIE", period_end, diff, tolerance))
    return results


def check_re_rollforward(curated: pd.DataFrame, instant_ends: list, tolerance: float) -> list[dict]:
    """Same beginning/ending-are-the-same-instant-series subtlety as CF cash
    continuity above: EQ_RE_BEGIN and EQ_RE_END are both us-gaap:StockholdersEquity
    (dimensioned to RetainedEarningsMember), an instant concept, keyed by instant
    date. "Beginning of FYt" is the value at the *prior* instant, not at FYt's own
    end date -- the prior implementation indexed both by the same key and always
    got net income back as the "diff" (an empty result list, in fact, since the
    stale EQ_RE_* ids from before the id-collision fix matched nothing)."""
    begin, ni, end = (_series(curated, x) for x in (EQ_RE_BEGIN, EQ_RE_NI, EQ_RE_END))
    prior = _prior_instant_map(instant_ends)
    results = []
    for period_end in sorted(prior):
        prior_instant = prior[period_end]
        if prior_instant is None:
            continue
        b, n, e = begin.get(prior_instant), ni.get(period_end), end.get(period_end)
        diff = None if (b is None or n is None or e is None) else (b + n) - e
        results.append(_mk("CHK_RE_ROLLFORWARD", period_end, diff, tolerance))
    return results


def check_rnci_rollforward(curated: pd.DataFrame, instant_ends: list, tolerance: float) -> list[dict]:
    begin, ni, acq, adj, dist, end = (
        _series(curated, x)
        for x in (EQ_RNCI_BEGIN, EQ_RNCI_NI, EQ_RNCI_ACQUISITION, EQ_RNCI_ADJUSTMENT, EQ_RNCI_DISTRIBUTIONS, EQ_RNCI_END)
    )
    prior = _prior_instant_map(instant_ends)
    results = []
    for period_end in sorted(prior):
        prior_instant = prior[period_end]
        if prior_instant is None:
            continue
        e = end.get(period_end)
        if e is None:
            continue
        b = begin.get(prior_instant)
        if b is None:
            results.append(_mk("CHK_RNCI_ROLLFORWARD", period_end, None, tolerance, "no beginning balance for this period"))
            continue
        diff = (
            b
            + ni.get(period_end, 0.0)
            + acq.get(period_end, 0.0)
            + adj.get(period_end, 0.0)
            - dist.get(period_end, 0.0)
            - e
        )
        results.append(_mk("CHK_RNCI_ROLLFORWARD", period_end, diff, tolerance))
    return results


def check_magnitude_guard(curated: pd.DataFrame, usd_magnitude_guard: float) -> list[dict]:
    """Catches the legacy bug pattern: a ratio-sized value sitting in a USD line."""
    usd_rows = curated[(curated["unit"] == "USD") & (curated["value"] != 0)]
    bad = usd_rows[usd_rows["value"].abs() < usd_magnitude_guard]
    results = []
    for _, row in bad.iterrows():
        results.append(
            {
                "id": "CHK_MAGNITUDE_GUARD",
                "period": row["period_end"],
                "diff": row["value"],
                "passed": False,
                "note": f"{row['line_id']}: |{row['value']}| < guard {usd_magnitude_guard}",
            }
        )
    if not results:
        results.append({"id": "CHK_MAGNITUDE_GUARD", "period": None, "diff": 0.0, "passed": True, "note": "no violations"})
    return results


def check_unit_guard(curated: pd.DataFrame, line_items: list[dict]) -> list[dict]:
    configured_unit = {li["id"]: li["unit"] for li in line_items if "unit" in li}
    results = []
    mismatches = 0
    for _, row in curated.iterrows():
        expected = configured_unit.get(row["line_id"])
        if expected is not None and expected != row["unit"]:
            mismatches += 1
            results.append(
                {
                    "id": "CHK_UNIT_GUARD",
                    "period": row["period_end"],
                    "diff": None,
                    "passed": False,
                    "note": f"{row['line_id']}: selected unit {row['unit']!r} != configured {expected!r}",
                }
            )
    if mismatches == 0:
        results.append({"id": "CHK_UNIT_GUARD", "period": None, "diff": 0.0, "passed": True, "note": "all units match configured line_items"})
    return results


def run_validate(curated: pd.DataFrame, line_items: list[dict], settings, instant_ends: list) -> list[dict]:
    tol = settings.validation.tolerance_usd_millions
    if tol is None:
        raise ValueError("settings.validation.tolerance_usd_millions is null -- pipeline refuses to validate (spec Sec 8.1)")
    tol_unscaled = tol * settings.render.usd_display_scale

    results: list[dict] = []
    results += check_subtotals(curated, line_items, tol_unscaled)
    results += check_bs_balance(curated, tol_unscaled)
    results += check_cf_net_change(curated, tol_unscaled)
    results += check_cf_cash_roll(curated, instant_ends, tol_unscaled)
    results += check_cf_cash_continuity(curated, instant_ends, tol_unscaled)
    results += check_cash_tie(curated, settings, tol_unscaled)
    results += check_ni_tie(curated, tol_unscaled)
    results += check_re_rollforward(curated, instant_ends, tol_unscaled)
    results += check_rnci_rollforward(curated, instant_ends, tol_unscaled)
    results += check_magnitude_guard(curated, settings.validation.usd_magnitude_guard)
    results += check_unit_guard(curated, line_items)
    return results


def render_report(results: list[dict]) -> str:
    lines = ["# Validation report", ""]
    lines.append("| check | period | diff | status | note |")
    lines.append("|---|---|---|---|---|")
    for r in results:
        status = "OK" if r["passed"] else ("N/A" if r["passed"] is None else "CHECK")
        diff = "" if r["diff"] is None else f"{r['diff']:,.2f}"
        period = "" if r["period"] is None else str(r["period"])
        lines.append(f"| {r['id']} | {period} | {diff} | {status} | {r.get('note','')} |")
    lines.append("")
    failures = [r for r in results if r["passed"] is False]
    lines.append(f"**{len(failures)} failing check row(s)** out of {len(results)}.")
    return "\n".join(lines)


def run_validate_and_report(
    settings,
    curated_path: str | pathlib.Path = "data/curated/historicals.parquet",
    config_dir: str | pathlib.Path = "config",
    reports_dir: str | pathlib.Path = "reports",
    instant_ends: list | None = None,
) -> tuple[pathlib.Path, list[dict]]:
    import yaml

    curated = pd.read_parquet(curated_path)
    line_items = yaml.safe_load((pathlib.Path(config_dir) / "line_items.yaml").read_text(encoding="utf-8"))
    if instant_ends is None:
        instant_ends = sorted(curated.loc[curated["days"].isna(), "period_end"].dropna().unique())

    results = run_validate(curated, line_items, settings, instant_ends)
    report_text = render_report(results)

    reports_dir = pathlib.Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.date.today().strftime("%Y%m%d")
    report_path = reports_dir / f"validation_{stamp}.md"
    report_path.write_text(report_text, encoding="utf-8")
    return report_path, results
