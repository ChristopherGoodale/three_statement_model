"""CLI entry point (spec Sec 9.9). Every command writes a run manifest (spec Sec 7.4).

`extract` and `discover` are implemented (Phases 1-2). The rest raise
NotImplementedError naming the phase that adds them -- see PLAN.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys

import pandas as pd
from dotenv import load_dotenv

from site3sm.checks import build_checks_layout, build_checks_write_set
from site3sm.config_models import load_settings
from site3sm.discovery import run_discovery, run_instance_extraction
from site3sm.edgar_client import EdgarClient
from site3sm.extract import run_extract
from site3sm.layout import TabLayout, build_period_columns
from site3sm.select_facts import determine_target_periods, run_select
from site3sm.sheets_writer import (
    SheetsWriter,
    build_readme_write_set,
    build_sources_write_set,
    build_write_set,
    write_dry_run,
)
from site3sm.validate import run_validate_and_report
from site3sm.verify import run_verify

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _git_commit() -> str | None:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, stderr=subprocess.DEVNULL)
            .decode()
            .strip()
        )
    except Exception:
        return None


def _write_manifest(command: str, target_sheet: str | None, extra: dict) -> pathlib.Path:
    runs_dir = REPO_ROOT / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_id = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    manifest = {
        "run_id": run_id,
        "git_commit": _git_commit(),
        "command": " ".join(sys.argv),
        "target_sheet": target_sheet,
        **extra,
    }
    path = runs_dir / f"manifest_{run_id}.json"
    path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return path


def cmd_extract(args: argparse.Namespace) -> None:
    settings = load_settings(REPO_ROOT / "config" / "settings.yaml")
    client = EdgarClient(
        cache_dir=REPO_ROOT / settings.sec.cache_dir,
        max_requests_per_second=settings.sec.max_requests_per_second,
        max_retries=settings.sec.max_retries,
        backoff_base_seconds=settings.sec.backoff_base_seconds,
        refresh=args.refresh,
    )
    facts_long, filings = run_extract(
        settings,
        client,
        staging_path=REPO_ROOT / "data" / "staging" / "facts_long.parquet",
        reports_dir=REPO_ROOT / "reports",
    )

    manifest_path = _write_manifest(
        "extract",
        target_sheet=None,
        extra={
            "filings_used": filings,
            "fact_counts": {"companyfacts": int(len(facts_long)), "instance": 0},
            "recasts_logged": 0,
            "missing_or_custom_lines": [],
            "validation": {"passed": None, "report": None},
            "verification": {"passed": None, "report": None},
        },
    )
    print(f"Extracted {len(facts_long)} facts -> data/staging/facts_long.parquet")
    print(f"Manifest: {manifest_path.relative_to(REPO_ROOT)}")


def cmd_discover(_args: argparse.Namespace) -> None:
    settings = load_settings(REPO_ROOT / "config" / "settings.yaml")
    report_path = run_discovery(
        settings,
        config_dir=REPO_ROOT / "config",
        reports_dir=REPO_ROOT / "reports",
        staging_path=REPO_ROOT / "data" / "staging" / "facts_long.parquet",
    )
    manifest_path = _write_manifest(
        "discover",
        target_sheet=None,
        extra={
            "filings_used": [],
            "fact_counts": {"companyfacts": None, "instance": None},
            "recasts_logged": 0,
            "missing_or_custom_lines": [],
            "validation": {"passed": None, "report": None},
            "verification": {"passed": None, "report": None},
        },
    )
    print(f"Discovery report: {report_path.relative_to(REPO_ROOT)}")
    print("Draft config/line_items.yaml and config/templates.yaml written -- all entries status: proposed.")
    print(f"Manifest: {manifest_path.relative_to(REPO_ROOT)}")


def cmd_instance(args: argparse.Namespace) -> None:
    settings = load_settings(REPO_ROOT / "config" / "settings.yaml")
    client = EdgarClient(
        cache_dir=REPO_ROOT / settings.sec.cache_dir,
        max_requests_per_second=settings.sec.max_requests_per_second,
        max_retries=settings.sec.max_retries,
        backoff_base_seconds=settings.sec.backoff_base_seconds,
        refresh=args.refresh,
    )
    instance_facts = run_instance_extraction(
        settings,
        client,
        config_dir=REPO_ROOT / "config",
        staging_path=REPO_ROOT / "data" / "staging" / "facts_long.parquet",
    )
    manifest_path = _write_manifest(
        "instance",
        target_sheet=None,
        extra={
            "filings_used": [],
            "fact_counts": {"companyfacts": None, "instance": int(len(instance_facts))},
            "recasts_logged": 0,
            "missing_or_custom_lines": [],
            "validation": {"passed": None, "report": None},
            "verification": {"passed": None, "report": None},
        },
    )
    print(f"Extracted {len(instance_facts)} instance-sourced facts, merged into data/staging/facts_long.parquet")
    print(f"Manifest: {manifest_path.relative_to(REPO_ROOT)}")


def cmd_select(_args: argparse.Namespace) -> None:
    settings = load_settings(REPO_ROOT / "config" / "settings.yaml")
    historicals_path, recasts_path = run_select(
        settings,
        facts_long_path=REPO_ROOT / "data" / "staging" / "facts_long.parquet",
        config_dir=REPO_ROOT / "config",
        curated_dir=REPO_ROOT / "data" / "curated",
    )
    import pandas as pd

    curated = pd.read_parquet(historicals_path)
    n_recasts = int(curated["recast_flag"].sum())
    manifest_path = _write_manifest(
        "select",
        target_sheet=None,
        extra={
            "filings_used": [],
            "fact_counts": {"companyfacts": None, "instance": None},
            "recasts_logged": n_recasts,
            "missing_or_custom_lines": [],
            "validation": {"passed": None, "report": None},
            "verification": {"passed": None, "report": None},
        },
    )
    print(f"Selected {len(curated)} (line_id, period) rows -> {historicals_path.relative_to(REPO_ROOT)}")
    print(f"Recast-flagged rows: {n_recasts}")
    print(f"Manifest: {manifest_path.relative_to(REPO_ROOT)}")


def cmd_validate(_args: argparse.Namespace) -> None:
    import sys as _sys

    import pandas as pd

    import yaml

    settings = load_settings(REPO_ROOT / "config" / "settings.yaml")
    facts_long = pd.read_parquet(REPO_ROOT / "data" / "staging" / "facts_long.parquet")
    line_items = yaml.safe_load((REPO_ROOT / "config" / "line_items.yaml").read_text(encoding="utf-8"))
    duration_ends, instant_ends = determine_target_periods(facts_long, settings, line_items)

    report_path, results = run_validate_and_report(
        settings,
        curated_path=REPO_ROOT / "data" / "curated" / "historicals.parquet",
        config_dir=REPO_ROOT / "config",
        reports_dir=REPO_ROOT / "reports",
        instant_ends=instant_ends,
    )
    failures = [r for r in results if r["passed"] is False]
    manifest_path = _write_manifest(
        "validate",
        target_sheet=None,
        extra={
            "filings_used": [],
            "fact_counts": {"companyfacts": None, "instance": None},
            "recasts_logged": 0,
            "missing_or_custom_lines": [],
            "validation": {"passed": len(failures) == 0, "report": str(report_path.relative_to(REPO_ROOT))},
            "verification": {"passed": None, "report": None},
        },
    )
    print(f"Validation report: {report_path.relative_to(REPO_ROOT)}")
    print(f"{len(failures)} failing check row(s) out of {len(results)}.")
    print(f"Manifest: {manifest_path.relative_to(REPO_ROOT)}")
    if failures:
        _sys.exit(1)


def _build_context(settings):
    import yaml

    line_items = yaml.safe_load((REPO_ROOT / "config" / "line_items.yaml").read_text(encoding="utf-8"))
    curated = pd.read_parquet(REPO_ROOT / "data" / "curated" / "historicals.parquet")

    instant_ends = sorted(curated.loc[curated["days"].isna(), "period_end"].dropna().unique())
    duration_ends = sorted(curated.loc[curated["days"].notna(), "period_end"].dropna().unique())

    tabs_in_config = sorted({li["tab"] for li in line_items if "tab" in li})
    layouts = {tab: TabLayout(tab, line_items) for tab in tabs_in_config}

    period_columns = build_period_columns(instant_ends, settings.periods.include_opening_balance_sheet)
    active_letters = ["E", "F", "G", "H"] if settings.periods.include_opening_balance_sheet else ["F", "G", "H"]

    return line_items, curated, layouts, period_columns, active_letters, duration_ends


def _cash_tie_config(settings) -> tuple[str, str] | None:
    cf_line = settings.cash_tie.cf_ending_cash_line_id
    bs_line = settings.cash_tie.bs_cash_line_id
    if not cf_line or not bs_line:
        return None
    return cf_line, bs_line


def cmd_build(args: argparse.Namespace) -> None:
    settings = load_settings(REPO_ROOT / "config" / "settings.yaml")
    line_items, curated, layouts, period_columns, active_letters, _duration_ends = _build_context(settings)

    cash_tie = _cash_tie_config(settings)
    layouts["Checks"] = build_checks_layout(cash_tie_configured=cash_tie is not None)
    # Hist_* cells hold SCALED values (divided by usd_display_scale, e.g. $455.5
    # for $455.5M) -- CHK_TOLERANCE has to be in that same scale to compare
    # against them on the sheet, NOT the raw unscaled dollar tolerance Python's
    # validate.py uses against curated (unscaled) data. Multiplying by
    # usd_display_scale here (as an earlier version did) silently made every
    # check compare a ~$10M-scale diff against a 100,000-scale threshold --
    # trivially "OK" regardless of the real discrepancy. Caught by reading the
    # live sheet back and noticing CHK_MASTER said OK when it plainly shouldn't
    # have for two known-gapped periods.
    tolerance_scaled = settings.validation.tolerance_usd_millions

    target_sheet = "prod" if args.promote else "scratch"
    tabs = [t.strip() for t in args.tabs.split(",")] if args.tabs else sorted(layouts)
    write_set: dict = {}
    for tab in tabs:
        if tab == "Sources":
            write_set.update(build_sources_write_set(curated, line_items))
        elif tab == "README":
            run_id = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            write_set.update(build_readme_write_set(run_id, target_sheet))
        elif tab == "Checks":
            if tolerance_scaled is None:
                raise ValueError(
                    "settings.validation.tolerance_usd_millions is null -- cannot build Checks (spec Sec 8.1)"
                )
            write_set.update(build_checks_write_set(layouts, active_letters, cash_tie, tolerance_scaled))
        else:
            write_set.update(
                build_write_set(tab, line_items, curated, layouts, period_columns, active_letters, settings)
            )

    if args.dry_run:
        plan_path = write_dry_run(write_set, REPO_ROOT / "reports")
        print(f"Dry-run plan ({len(write_set)} cells): {plan_path.relative_to(REPO_ROOT)}")
    else:
        sheet_id = os.environ["SHEET_ID_PROD"] if args.promote else os.environ["SHEET_ID_SCRATCH"]
        credentials_path = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
        writer = SheetsWriter(credentials_path)
        writer.write(sheet_id, write_set)
        print(f"Wrote {len(write_set)} cells to {target_sheet} ({', '.join(tabs)})")

    manifest_path = _write_manifest(
        "build",
        target_sheet=target_sheet,
        extra={
            "filings_used": [],
            "fact_counts": {"companyfacts": None, "instance": None},
            "recasts_logged": 0,
            "missing_or_custom_lines": [],
            "validation": {"passed": None, "report": None},
            "verification": {"passed": None, "report": None},
        },
    )
    print(f"Manifest: {manifest_path.relative_to(REPO_ROOT)}")


def cmd_verify(args: argparse.Namespace) -> None:
    settings = load_settings(REPO_ROOT / "config" / "settings.yaml")
    line_items, curated, layouts, period_columns, active_letters, _duration_ends = _build_context(settings)

    cash_tie = _cash_tie_config(settings)
    layouts["Checks"] = build_checks_layout(cash_tie_configured=cash_tie is not None)
    # See the matching comment in cmd_build: must match the SCALE of Hist_* cells
    # (already divided by usd_display_scale), not the raw unscaled tolerance.
    tolerance_scaled = settings.validation.tolerance_usd_millions

    # README is excluded: its content (last run ID) is a snapshot of whatever build
    # run wrote it, not a deterministic function of the data -- re-verifying it
    # would just compare against a freshly-generated, different run ID every time.
    tabs = sorted(layouts) + ["Sources"]
    write_set: dict = {}
    for tab in tabs:
        if tab == "Sources":
            write_set.update(build_sources_write_set(curated, line_items))
        elif tab == "Checks":
            if tolerance_scaled is not None:
                write_set.update(build_checks_write_set(layouts, active_letters, cash_tie, tolerance_scaled))
        else:
            write_set.update(
                build_write_set(tab, line_items, curated, layouts, period_columns, active_letters, settings)
            )

    target_sheet = "prod" if args.promote else "scratch"
    sheet_id = os.environ["SHEET_ID_PROD"] if args.promote else os.environ["SHEET_ID_SCRATCH"]
    credentials_path = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]

    report_path, results = run_verify(sheet_id, write_set, credentials_path, REPO_ROOT / "reports")
    failures = [r for r in results if r["passed"] is False]

    manifest_path = _write_manifest(
        "verify",
        target_sheet=target_sheet,
        extra={
            "filings_used": [],
            "fact_counts": {"companyfacts": None, "instance": None},
            "recasts_logged": 0,
            "missing_or_custom_lines": [],
            "validation": {"passed": None, "report": None},
            "verification": {"passed": len(failures) == 0, "report": str(report_path.relative_to(REPO_ROOT))},
        },
    )
    print(f"Verify report: {report_path.relative_to(REPO_ROOT)}")
    print(f"{len(failures)} failing check(s) out of {len(results)}.")
    print(f"Manifest: {manifest_path.relative_to(REPO_ROOT)}")
    if failures:
        sys.exit(1)


def _not_implemented(phase: str):
    def handler(_args: argparse.Namespace) -> None:
        raise NotImplementedError(f"`{phase}` is not implemented yet -- see PLAN.md for phase status.")

    return handler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m site3sm.run")
    sub = parser.add_subparsers(dest="command", required=True)

    p_extract = sub.add_parser("extract", help="Discover filings and pull companyfacts (Phase 1)")
    p_extract.add_argument("--refresh", action="store_true", help="Bypass the disk cache")
    p_extract.set_defaults(func=cmd_extract)

    p_discover = sub.add_parser("discover", help="XBRL concept discovery (Phase 2)")
    p_discover.set_defaults(func=cmd_discover)

    p_instance = sub.add_parser(
        "instance", help="Pull real values for approved custom/dimensional lines (Phase 2, post Gate-2)"
    )
    p_instance.add_argument("--refresh", action="store_true", help="Bypass the disk cache")
    p_instance.set_defaults(func=cmd_instance)

    p_select = sub.add_parser("select", help="Select and dedupe facts into curated historicals (Phase 3)")
    p_select.set_defaults(func=cmd_select)

    p_validate = sub.add_parser("validate", help="Validate accounting identities (Phase 3)")
    p_validate.set_defaults(func=cmd_validate)

    p_build = sub.add_parser("build", help="Render tabs to the sheet (Phase 4+)")
    p_build.add_argument("--tabs")
    p_build.add_argument("--dry-run", action="store_true")
    p_build.add_argument("--promote", action="store_true")
    p_build.set_defaults(func=cmd_build)

    p_verify = sub.add_parser("verify", help="Read back and verify the sheet (Phase 4+)")
    p_verify.add_argument("--promote", action="store_true")
    p_verify.set_defaults(func=cmd_verify)

    p_all = sub.add_parser("all", help="Run every stage in order")
    p_all.add_argument("--dry-run", action="store_true")
    p_all.add_argument("--promote", action="store_true")
    p_all.set_defaults(func=_not_implemented("all (added once every stage exists)"))

    return parser


def main(argv: list[str] | None = None) -> None:
    load_dotenv(REPO_ROOT / ".env")
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
