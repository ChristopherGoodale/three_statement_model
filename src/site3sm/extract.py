"""Filing discovery and companyfacts normalization into facts_long (spec Sec 9.2).

Phase 1 scope only: standard (companyfacts) facts. Instance-document parsing for
custom/dimensional facts is Phase 2 (discovery.py) and is not implemented here.
"""
from __future__ import annotations

import datetime as dt
import pathlib

import pandas as pd

from site3sm.config_models import Settings
from site3sm.edgar_client import EdgarClient

FACTS_LONG_COLUMNS = [
    "concept",
    "taxonomy_type",
    "dimensions",
    "unit",
    "period_type",
    "start",
    "end",
    "days",
    "val",
    "accn",
    "form",
    "filed",
    "source",
    "raw_path",
]

TENK_FORMS = {"10-K", "10-K/A"}


def discover_filings(client: EdgarClient, settings: Settings) -> tuple[list[dict], pathlib.Path]:
    """Return the 10-K/10-K/A filings from the submissions API, most recent first.

    Does not decide which ones are "in scope" for balance-sheet coverage -- that
    judgment (spec Sec 9.2 "add the earlier 10-Ks required...") depends on which
    fiscal year-ends companyfacts' own returned facts actually cover, since
    companyfacts aggregates facts across every filing already. This function's
    job is just the accession-number/filed-date/form audit trail (spec Sec 9.2,
    run manifest `filings_used`), never hardcoded (spec Sec 4).
    """
    cik10 = settings.issuer.cik10
    subs, raw_path = client.submissions(cik10)

    recent = subs.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accns = recent.get("accessionNumber", [])
    filed = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    primary_docs = recent.get("primaryDocument", [])

    filings = []
    for form, accn, filed_date, report_date, primary_doc in zip(
        forms, accns, filed, report_dates, primary_docs
    ):
        if form in TENK_FORMS:
            filings.append(
                {
                    "form": form,
                    "accn": accn,
                    "filed": filed_date,
                    "report_date": report_date,
                    "primary_document": primary_doc,
                }
            )

    filings.sort(key=lambda f: f["filed"], reverse=True)
    return filings, raw_path


def _period_days(start: dt.date | None, end: dt.date) -> int | None:
    if start is None:
        return None
    return (end - start).days + 1


def normalize_companyfacts(companyfacts: dict, raw_path: pathlib.Path) -> pd.DataFrame:
    """Flatten the companyfacts JSON into one row per fact per (concept, unit, period, filing).

    Every row is `source = companyfacts`, `dimensions = {}` (entity-wide facts only --
    companyfacts never returns dimensional or custom-taxonomy facts, spec Sec 3).
    Never keys or filters on `fy`/`fp` (spec Sec 9.2) -- those fields aren't even
    carried into the output.
    """
    rows: list[dict] = []
    facts = companyfacts.get("facts", {})
    raw_path_str = str(raw_path)

    for taxonomy_type, tags in facts.items():
        for tag, tag_data in tags.items():
            concept = f"{taxonomy_type}:{tag}"
            units = tag_data.get("units", {})
            for unit, entries in units.items():
                for entry in entries:
                    start_raw = entry.get("start")
                    end_raw = entry["end"]
                    start = dt.date.fromisoformat(start_raw) if start_raw else None
                    end = dt.date.fromisoformat(end_raw)
                    period_type = "duration" if start is not None else "instant"
                    rows.append(
                        {
                            "concept": concept,
                            "taxonomy_type": "standard",
                            "dimensions": "{}",
                            "unit": unit,
                            "period_type": period_type,
                            "start": start,
                            "end": end,
                            "days": _period_days(start, end),
                            "val": entry["val"],
                            "accn": entry["accn"],
                            "form": entry["form"],
                            "filed": dt.date.fromisoformat(entry["filed"]),
                            "source": "companyfacts",
                            "raw_path": raw_path_str,
                        }
                    )

    df = pd.DataFrame(rows, columns=FACTS_LONG_COLUMNS)
    df = df.drop_duplicates().reset_index(drop=True)
    return df


def summarize(facts_long: pd.DataFrame, filings: list[dict]) -> str:
    by_source = facts_long.groupby("source").size().to_dict()
    by_form = facts_long.groupby("form").size().to_dict()

    lines = [
        "# Extract summary",
        "",
        f"Total facts: {len(facts_long)}",
        "",
        "## By source",
        *(f"- {k}: {v}" for k, v in sorted(by_source.items())),
        "",
        "## By form",
        *(f"- {k}: {v}" for k, v in sorted(by_form.items())),
        "",
        "## 10-K / 10-K/A filings discovered",
        *(f"- {f['form']} accn={f['accn']} filed={f['filed']} report_date={f['report_date']}" for f in filings),
    ]
    return "\n".join(lines)


def run_extract(
    settings: Settings,
    client: EdgarClient,
    staging_path: str | pathlib.Path = "data/staging/facts_long.parquet",
    reports_dir: str | pathlib.Path = "reports",
) -> tuple[pd.DataFrame, list[dict]]:
    filings, submissions_raw_path = discover_filings(client, settings)
    companyfacts, companyfacts_raw_path = client.companyfacts(settings.issuer.cik10)
    facts_long = normalize_companyfacts(companyfacts, companyfacts_raw_path)

    assert facts_long["accn"].notna().all(), "every row must have accn (spec Sec 9.2 acceptance)"
    assert facts_long["raw_path"].notna().all(), "every row must have raw_path (spec Sec 9.2 acceptance)"
    assert not facts_long.duplicated().any(), "facts_long must have no duplicate rows on the full key"

    staging_path = pathlib.Path(staging_path)
    staging_path.parent.mkdir(parents=True, exist_ok=True)
    facts_long.to_parquet(staging_path, index=False)

    reports_dir = pathlib.Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    report_path = reports_dir / f"extract_summary_{stamp}.md"
    report_path.write_text(summarize(facts_long, filings), encoding="utf-8")

    return facts_long, filings
