"""Fact selection: recast resolution, duration-window filtering, line_id mapping
(spec Sec 9.4). Produces data/curated/historicals.parquet (one row per line_id x
period_end) and data/curated/recasts.parquet (every candidate value, pre-dedup).
"""
from __future__ import annotations

import json
import pathlib

import pandas as pd
import yaml

TENK_FORMS = {"10-K", "10-K/A"}
DURATION_DAYS_MIN = 357
DURATION_DAYS_MAX = 378

CURATED_COLUMNS = [
    "line_id",
    "statement",
    "period_start",
    "period_end",
    "days",
    "value",
    "unit",
    "sign_applied",
    "concept",
    "dimensions",
    "accn",
    "form",
    "filed",
    "source",
    "raw_path",
    "recast_flag",
]


class MissingRequiredFactError(RuntimeError):
    pass


class AmbiguousFactError(RuntimeError):
    pass


class MultipleMatchesError(RuntimeError):
    pass


def filter_10k_facts(facts_long: pd.DataFrame) -> pd.DataFrame:
    df = facts_long[facts_long["form"].isin(TENK_FORMS)].copy()
    instant = df["period_type"] == "instant"
    duration_ok = df["days"].between(DURATION_DAYS_MIN, DURATION_DAYS_MAX)
    return df[instant | duration_ok].copy()


def determine_target_periods(facts_long: pd.DataFrame, settings, line_items: list[dict]) -> tuple[list, list]:
    """The N most recent distinct period-ends observed in any 10-K fact for a
    reference face concept -- never a hardcoded date (spec Sec 4). N =
    historical_fiscal_years for durations; + 1 more for instants if
    include_opening_balance_sheet.

    Anchoring on one non-dimensional face concept per period type (rather than
    "any fact anywhere in the filing") matters: without it, incidental dates from
    footnote disclosures (e.g. a subsequent-event balance, or a quarterly figure
    cited in a note) can be more recent than the real fiscal year-end and crowd the
    actual balance sheet dates out of the "N most recent" window. BS_TOTAL_ASSETS /
    IS_NET_SALES are guaranteed present, non-dimensional, and reported every year.
    """
    tenk = filter_10k_facts(facts_long)

    def _reference_concept(line_id: str) -> str | None:
        return next((li["concepts"][0] for li in line_items if li["id"] == line_id and "concepts" in li), None)

    bs_ref = _reference_concept("BS_TOTAL_ASSETS")
    is_ref = _reference_concept("IS_NET_SALES")

    instants = tenk[tenk["period_type"] == "instant"]
    if bs_ref:
        instants = instants[(instants["concept"] == bs_ref) & (instants["dimensions"] == "{}")]
    durations = tenk[tenk["period_type"] == "duration"]
    if is_ref:
        durations = durations[(durations["concept"] == is_ref) & (durations["dimensions"] == "{}")]

    duration_ends = sorted(durations["end"].dropna().unique(), reverse=True)[: settings.periods.historical_fiscal_years]
    n_instants = settings.periods.historical_fiscal_years + (1 if settings.periods.include_opening_balance_sheet else 0)
    instant_ends = sorted(instants["end"].dropna().unique(), reverse=True)[:n_instants]
    return sorted(duration_ends), sorted(instant_ends)


def resolve_recasts(tenk: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Group by the (concept, unit, dimensions, start, end) fact key. Log every
    candidate value BEFORE dedup (spec Sec 9.4: 'Write recasts.parquet before
    deduplication'). Resolution rule: most-recently-filed value wins. A genuine tie
    (two different values both filed on the same date) is an error, not a coin flip."""
    key_cols = ["concept", "unit", "dimensions", "start", "end"]
    recast_rows: list[dict] = []
    resolved_rows: list[dict] = []

    for key, group in tenk.groupby(key_cols, dropna=False):
        group = group.sort_values("filed")
        is_recast = group["val"].nunique() > 1
        latest_filed = group["filed"].max()
        top = group[group["filed"] == latest_filed]
        if top["val"].nunique() > 1:
            raise AmbiguousFactError(
                f"Multiple differing values filed on the same date for "
                f"concept={key[0]} unit={key[1]} dims={key[2]} start={key[3]} end={key[4]}: "
                f"{top[['accn', 'val']].to_dict('records')}"
            )
        chosen = top.iloc[0]
        if is_recast:
            recast_rows.append(
                {
                    "concept": key[0],
                    "unit": key[1],
                    "dimensions": key[2],
                    "start": key[3],
                    "end": key[4],
                    "values": group["val"].tolist(),
                    "accns": group["accn"].tolist(),
                    "selected_accn": chosen["accn"],
                }
            )
        resolved_rows.append(chosen)

    resolved = pd.DataFrame(resolved_rows).reset_index(drop=True)
    recasts = pd.DataFrame(recast_rows)
    return resolved, recasts


def _dims_key(line_item: dict) -> str:
    return json.dumps(line_item.get("dimensions") or {})


def select_facts(
    facts_long: pd.DataFrame, line_items: list[dict], settings
) -> tuple[pd.DataFrame, pd.DataFrame]:
    tenk = filter_10k_facts(facts_long)
    resolved, recasts = resolve_recasts(tenk)
    duration_ends, instant_ends = determine_target_periods(facts_long, settings, line_items)

    curated_rows: list[dict] = []
    missing: list[tuple[str, object]] = []

    for li in line_items:
        if li.get("status") != "approved" or "concepts" not in li:
            continue  # R2: unapproved config is never used; headers have no concepts

        period_ends = instant_ends if li["period_type"] == "instant" else duration_ends
        dims_json = _dims_key(li)

        for period_end in period_ends:
            match = None
            for concept in li["concepts"]:  # ordered fallback list
                candidates = resolved[
                    (resolved["concept"] == concept)
                    & (resolved["unit"] == li["unit"])
                    & (resolved["dimensions"] == dims_json)
                    & (resolved["end"] == period_end)
                ]
                if len(candidates) > 1:
                    raise MultipleMatchesError(
                        f"{li['id']} @ {period_end}: {len(candidates)} surviving matches for concept={concept} "
                        "(should be impossible after recast resolution -- indicates a fact-key collision)"
                    )
                if len(candidates) == 1:
                    match = candidates.iloc[0]
                    break

            if match is None:
                if li.get("allow_missing", False) or not li.get("required", True):
                    continue
                missing.append((li["id"], period_end))
                continue

            sign = li.get("sign", 1)
            recast_flag = False
            if len(recasts):
                recast_flag = bool(
                    (
                        (recasts["concept"] == match["concept"])
                        & (recasts["unit"] == match["unit"])
                        & (recasts["dimensions"] == match["dimensions"])
                        & (recasts["end"] == match["end"])
                    ).any()
                )
            curated_rows.append(
                {
                    "line_id": li["id"],
                    "statement": li["tab"].replace("Hist_", ""),
                    "period_start": match["start"],
                    "period_end": period_end,
                    "days": match["days"],
                    "value": match["val"] * sign,
                    "unit": li["unit"],
                    "sign_applied": sign,
                    "concept": match["concept"],
                    "dimensions": dims_json,
                    "accn": match["accn"],
                    "form": match["form"],
                    "filed": match["filed"],
                    "source": match["source"],
                    "raw_path": match["raw_path"],
                    "recast_flag": recast_flag,
                }
            )

    if missing:
        detail = "\n".join(f"  - {line_id} @ {period_end}" for line_id, period_end in missing)
        raise MissingRequiredFactError(f"Missing required facts (R3), rendering cannot proceed:\n{detail}")

    curated = pd.DataFrame(curated_rows, columns=CURATED_COLUMNS)
    if curated.duplicated(subset=["line_id", "period_end"]).any():
        raise MultipleMatchesError("duplicate (line_id, period_end) rows in curated output -- should be impossible")

    return curated, recasts


def run_select(
    settings,
    facts_long_path: str | pathlib.Path = "data/staging/facts_long.parquet",
    config_dir: str | pathlib.Path = "config",
    curated_dir: str | pathlib.Path = "data/curated",
) -> tuple[pathlib.Path, pathlib.Path]:
    facts_long = pd.read_parquet(facts_long_path)
    line_items = yaml.safe_load((pathlib.Path(config_dir) / "line_items.yaml").read_text(encoding="utf-8"))

    curated, recasts = select_facts(facts_long, line_items, settings)

    curated_dir = pathlib.Path(curated_dir)
    curated_dir.mkdir(parents=True, exist_ok=True)
    historicals_path = curated_dir / "historicals.parquet"
    recasts_path = curated_dir / "recasts.parquet"
    curated.to_parquet(historicals_path, index=False)
    if len(recasts):
        recasts.to_parquet(recasts_path, index=False)
    elif recasts_path.exists():
        recasts_path.unlink()

    return historicals_path, recasts_path
