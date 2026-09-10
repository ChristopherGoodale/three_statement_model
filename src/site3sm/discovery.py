"""XBRL concept discovery (spec Sec 9.3): parser spike, presentation/calculation
linkbase enumeration for the four primary statements, draft config/line_items.yaml
and proposed checks in config/templates.yaml.

Parser: edgartools (see PARSER_SPIKE_NOTES below for why).
"""
from __future__ import annotations

import datetime as dt
import pathlib
import re

import pandas as pd
import yaml

from site3sm.config_models import Settings

PARSER_SPIKE_NOTES = """\
## Parser spike

Three candidates were in scope per spec Sec 5: Arelle, edgartools, direct `lxml` parsing.

**edgartools was spiked directly against the anchor filing (CIK 0001650729, accn
{accn})** using its `Filing.xbrl().statements` API. Result: a single call per statement
(`income_statement()`, `balance_sheet()`, `cash_flow_statement()`, `statement_of_equity()`)
returns a dataframe already carrying presentation order (`level`, `parent_concept`),
preferred labels, calculation-linkbase weights (`weight`, matching sign conventions
observed against the filing -- e.g. COGS/SG&A/interest/tax all resolve to `weight=-1`
relative to their subtotal parent), balance type (`balance`: debit/credit), and full
dimensional context (`dimension`, `dimension_axis`, `dimension_member`) -- including
custom (`site:`) concepts and `StatementEquityComponentsAxis`-dimensioned equity
roll-forward facts that companyfacts does not expose (confirmed by cross-checking
`site:FinanceLeaseRightofUseAssetAmortizationAndDepreciation` and the RNCI roll-forward
concepts against Phase 1's `facts_long.parquet` -- absent there, present here). This
satisfies every criterion in spec Sec 9.3 (statement roles, dimension handling, label
and negated-label roles via the resolved `weight`/`label` pair, calculation weights) in
one library, against the real filing, not a synthetic test.

**Arelle** was not separately installed and spiked. It is the more authoritative,
fully spec-compliant XBRL processor (used as a reference implementation across the
industry), but it is also the heaviest of the three (full validation engine, optional
GUI/plugin architecture) and its API is lower-level -- it would hand back raw
presentation/calculation linkbase relationships and require this pipeline to build the
same row-flattening, dimension-resolution, and weight-sign logic that edgartools
already provides directly. Given edgartools met every acceptance criterion against the
actual anchor filing on the first attempt, spiking Arelle as well was judged not worth
the additional install/engineering cost for v1. This is a documented trade-off, not a
claim that Arelle was tested and found lacking -- it wasn't tested.

**Direct `lxml` parsing** was rejected for the same reason in the other direction: it
would mean re-implementing presentation-linkbase locator/arc resolution and
calculation-linkbase weight resolution by hand, which is exactly the failure-prone
work a maintained library exists to avoid.

**Recommendation: edgartools.** License: MIT. Install footprint: pandas/pydantic/httpx-based,
no heavy binary dependencies. Actively maintained (version 5.57.0 installed during this
spike). Chosen and used for the extraction below.
"""

STATEMENTS: list[tuple[str, str, str]] = [
    ("IS", "Income Statement", "income_statement"),
    ("BS", "Balance Sheet", "balance_sheet"),
    ("CF", "Cash Flow Statement", "cash_flow_statement"),
    ("EQ", "Equity", "statement_of_equity"),
]

PERIOD_TYPE_BY_STATEMENT = {"IS": "duration", "BS": "instant", "CF": "duration", "EQ": "duration"}


def _concept_to_qname(concept: str) -> str:
    """'us-gaap_CashAndCashEquivalentsAtCarryingValue' -> 'us-gaap:CashAndCashEquivalentsAtCarryingValue'."""
    return concept.replace("_", ":", 1)


def _taxonomy_type(concept: str) -> str:
    prefix = concept.split("_", 1)[0]
    return "standard" if prefix in {"us-gaap", "dei", "srt", "ifrs-full"} else "custom"


def _slug(label: str, used: set[str]) -> str:
    label = re.sub(r"\(Note[s]? [\w, ]+\)", "", label)  # strip footnote refs
    label = re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_").upper()
    label = re.sub(r"_+", "_", label)
    if not label:
        label = "LINE"
    label = label[:60]
    candidate = label
    i = 2
    while candidate in used:
        candidate = f"{label}_{i}"
        i += 1
    used.add(candidate)
    return candidate


def _is_share_concept(concept: str, label: str) -> bool:
    hay = f"{concept} {label}".lower()
    return "shares" in hay and "per share" not in hay


def _is_per_share_concept(concept: str, label: str) -> bool:
    hay = f"{concept} {label}".lower()
    return "per share" in hay or "pershare" in hay.replace(" ", "")


def _infer_period_type(concept: str, statement_prefix: str, companyfacts_period_types: dict[str, str]) -> str:
    """Period type is a property of the concept itself, not the statement it's rendered
    on -- e.g. CF's beginning/ending cash memo lines are instants even though the rest
    of the Cash Flow Statement is durations. Prefer the concept's actual observed type
    in companyfacts; fall back to the statement's dominant type only for custom concepts
    absent from companyfacts (where there's nothing else to go on yet)."""
    qname = _concept_to_qname(concept)
    return companyfacts_period_types.get(qname, PERIOD_TYPE_BY_STATEMENT[statement_prefix])


def _infer_unit(concept: str, label: str, companyfacts_units: dict[str, str]) -> str:
    qname = _concept_to_qname(concept)
    if qname in companyfacts_units:
        u = companyfacts_units[qname]
        if u == "USD":
            return "USD"
        if u == "shares":
            return "shares"
        if u in {"USD/shares", "USD-per-shares"}:
            return "USD/shares"
        return u
    if _is_per_share_concept(concept, label):
        return "USD/shares"
    if _is_share_concept(concept, label):
        return "shares"
    return "USD"


def face_rows_for_statement(prefix: str, df: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Split a statement dataframe into (included face rows, excluded rows w/ reason).

    BS/IS/CF: face = non-dimensional rows only. Segment/tax-jurisdiction dimensional
    breakouts are excluded as out-of-scope segment data (spec Sec 4).

    EQ: the roll-forward's real values live partly in non-dimensional rows (RNCI, the
    consolidated equity total/NI/OCI) and partly in StatementEquityComponentsAxis-
    dimensioned rows (the only place per-component -- e.g. Retained Earnings -- values
    exist, spec Sec 3's explicit requirement). Other axes on EQ (ConsolidatedEntitiesAxis
    "Parent Company", equity-method-investee schedules) are excluded as duplicative or
    out-of-scope.
    """
    excluded: list[dict] = []
    if prefix == "EQ":
        keep_mask = (~df["dimension"]) | (df["dimension_axis"] == "us-gaap:StatementEquityComponentsAxis")
    else:
        keep_mask = ~df["dimension"]

    for _, row in df[~keep_mask].iterrows():
        excluded.append(
            {
                "statement": prefix,
                "concept": row["concept"],
                "label": row["label"],
                "reason": (
                    f"dimensional breakdown on {row.get('dimension_axis')} "
                    f"({row.get('dimension_member_label')}) -- out of scope (segment/entity axis, spec Sec 4)"
                ),
            }
        )
    return df[keep_mask].copy(), excluded


def build_line_items_and_checks(
    face_rows_by_stmt: dict[str, pd.DataFrame],
    companyfacts_units: dict[str, str],
    companyfacts_period_types: dict[str, str],
) -> tuple[list[dict], list[dict]]:
    line_items: list[dict] = []
    checks: list[dict] = []

    for prefix, _label, _accessor in STATEMENTS:
        df = face_rows_by_stmt[prefix]
        tab = f"Hist_{prefix}"
        used_ids: set[str] = set()
        concept_to_id: dict[str, str] = {}
        order = 100

        non_abstract = df[~df["abstract"]].copy()
        parents_with_children = set(non_abstract["parent_concept"].dropna().unique())

        row_ids: dict[int, str] = {}
        for idx, row in df.iterrows():
            if row["abstract"]:
                line_id = f"HDR_{prefix}_{_slug(row['label'], used_ids)}"
                line_items.append(
                    {
                        "id": line_id,
                        "tab": tab,
                        "order": order,
                        "label": row["label"],
                        "kind": "header",
                        "status": "proposed",
                    }
                )
                order += 10
                continue

            concept = row["concept"]
            is_formula_line = prefix != "EQ" and concept in parents_with_children and concept not in concept_to_id
            line_id = f"{prefix}_{_slug(row['label'], used_ids)}"
            row_ids[idx] = line_id
            if concept not in concept_to_id:
                concept_to_id[concept] = line_id

            entry: dict = {
                "id": line_id,
                "tab": tab,
                "order": order,
                "label": row["label"],
                "concepts": [_concept_to_qname(concept)],
                "period_type": _infer_period_type(concept, prefix, companyfacts_period_types),
                "unit": _infer_unit(concept, row["label"], companyfacts_units),
                "sign": 1,  # proposed default; owner confirms per negated-label review (spec Sec 8.2)
                "required": True,
                "allow_missing": False,
                "status": "proposed",
            }
            if prefix == "EQ" and bool(row.get("dimension")):
                axis = row["dimension_axis"]
                member = _concept_to_qname(row["dimension_member"])
                entry["dimensions"] = {axis: member}
                entry["id"] = f"EQ_{_slug(row['dimension_member_label'] + ' ' + row['label'], used_ids)}"
                line_id = entry["id"]
                row_ids[idx] = line_id

            entry["kind"] = "formula" if is_formula_line else "fact"
            entry["taxonomy_type"] = _taxonomy_type(concept)
            line_items.append(entry)
            order += 10

        if prefix == "EQ":
            continue  # roll-forward checks (RE/RNCI) are composed later, not per-line formulas

        # second pass: fill in formula templates for parent/subtotal lines using
        # calculation-linkbase weights already resolved by edgartools into `weight`
        by_id = {li["id"]: li for li in line_items if li["tab"] == tab}
        children_by_parent: dict[str, list[tuple[str, float]]] = {}
        for idx, row in non_abstract.iterrows():
            parent = row["parent_concept"]
            if pd.isna(parent) or idx not in row_ids:
                continue
            weight = row["weight"] if pd.notna(row["weight"]) else 1.0
            children_by_parent.setdefault(parent, []).append((row_ids[idx], weight))

        for concept, line_id in concept_to_id.items():
            children = children_by_parent.get(concept)
            entry = by_id.get(line_id)
            if not children or entry is None or entry["kind"] != "formula":
                continue
            terms = []
            for child_id, weight in children:
                sign = "+" if weight >= 0 else "-"
                terms.append(f"{sign} {{{child_id}}}")
            template = " ".join(terms).lstrip("+ ").strip()
            if template.startswith("- "):
                template = "-" + template[1:]
            entry["template"] = template
            entry["reported_concepts"] = [_concept_to_qname(concept)]
            entry.pop("sign", None)
            checks.append(
                {
                    "id": f"CHK_SUBTOTAL_{line_id}",
                    "description": f"{entry['label']} (formula) vs. reported {entry['reported_concepts'][0]}",
                    "reported_concepts": entry["reported_concepts"],
                    "line_id": f"{tab}!{line_id}",
                    "periods": "historical",
                    "status": "proposed",
                    "note": (
                        "no sheet-level template yet -- Phase 3 validate.py compares the Python-computed "
                        "formula value against reported_concepts (spec Sec 9.5 item 1); the live sheet-formula "
                        "version of this check is finalized in Phase 5 once formulas.py's token grammar exists"
                    ),
                }
            )

    return line_items, checks


FIXED_IDENTITY_CHECKS = [
    {
        "id": "CHK_BS_BALANCE",
        "description": "Total assets minus (total liabilities + RNCI + total stockholders' equity)",
        "status": "proposed",
        "note": "line IDs filled in once BS totals/RNCI/SE line_items are approved (Gate 2)",
    },
    {
        "id": "CHK_CF_NET_CHANGE",
        "description": "Sum of CF section totals and reconciling lines minus net change in cash",
        "status": "proposed",
    },
    {
        "id": "CHK_CF_CASH_ROLL",
        "description": "Beginning cash + net change minus ending cash",
        "status": "proposed",
    },
    {
        "id": "CHK_CF_CASH_CONTINUITY",
        "description": "CF beginning cash(t) minus CF ending cash(t-1)",
        "status": "proposed",
    },
    {
        "id": "CHK_CASH_TIE",
        "description": "CF ending cash minus BS cash",
        "status": "proposed",
        "note": "blocked on settings.cash_tie.* (still null -- open question, PLAN.md Sec 5 item 5)",
    },
    {
        "id": "CHK_NI_TIE",
        "description": "IS consolidated net income minus CF starting net income",
        "status": "proposed",
    },
    {
        "id": "CHK_RE_ROLLFORWARD",
        "description": "Prior retained earnings + approved EQ components minus current retained earnings",
        "status": "proposed",
        "note": "components: EQ lines dimensioned to StatementEquityComponentsAxis=RetainedEarningsMember",
    },
    {
        "id": "CHK_RNCI_ROLLFORWARD",
        "description": "Prior RNCI + approved EQ components minus current RNCI",
        "status": "proposed",
        "note": "components: EQ_ACQUISITION_OF_NON_CONTROLLING_INTEREST etc. -- see Hist_EQ non-dimensional rows",
    },
]


def render_report(
    accn: str,
    filed_date: str,
    line_items: list[dict],
    excluded: list[dict],
    face_rows_by_stmt: dict[str, pd.DataFrame],
) -> str:
    lines = ["# Concept discovery report", "", f"Anchor filing: 10-K accn={accn} filed={filed_date}", ""]
    lines.append(PARSER_SPIKE_NOTES.format(accn=accn))
    lines.append("")

    for prefix, label, _accessor in STATEMENTS:
        stmt_items = [li for li in line_items if li["tab"] == f"Hist_{prefix}"]
        lines.append(f"## {label} ({prefix}) -- {len(stmt_items)} lines mapped")
        lines.append("")
        lines.append("| id | kind | label | concept(s) | taxonomy | in companyfacts |")
        lines.append("|---|---|---|---|---|---|")
        for li in stmt_items:
            if li["kind"] == "header":
                lines.append(f"| {li['id']} | header | {li['label']} | | | |")
                continue
            concept = li["concepts"][0]
            in_cf = "yes" if li.get("_in_companyfacts") else "no"
            lines.append(
                f"| {li['id']} | {li['kind']} | {li['label']} | `{concept}` | {li.get('taxonomy_type','')} | {in_cf} |"
            )
        lines.append("")

    lines.append("## Excluded rows (dimensional breakdowns not carried into line_items.yaml)")
    lines.append("")
    if not excluded:
        lines.append("None.")
    else:
        lines.append("| statement | concept | label | reason |")
        lines.append("|---|---|---|---|")
        for ex in excluded:
            lines.append(f"| {ex['statement']} | `{ex['concept']}` | {ex['label']} | {ex['reason']} |")
    lines.append("")

    lines.append("## Coverage check (spec Sec 9.3 acceptance)")
    lines.append("")
    lines.append(
        "Every non-abstract face row from `income_statement()`, `balance_sheet()`, "
        "`cash_flow_statement()`, and `statement_of_equity()` (per the inclusion rule above) "
        "appears in a table above with a line_id. Rows not carried into `line_items.yaml` are "
        "listed in the Excluded table with an explicit reason -- none were silently dropped."
    )
    return "\n".join(lines)


def run_discovery(
    settings: Settings,
    config_dir: str | pathlib.Path = "config",
    reports_dir: str | pathlib.Path = "reports",
    staging_path: str | pathlib.Path = "data/staging/facts_long.parquet",
) -> pathlib.Path:
    import os

    from dotenv import load_dotenv

    load_dotenv()
    from edgar import Company

    os.environ.setdefault("EDGAR_IDENTITY", os.environ.get("SEC_USER_AGENT", ""))

    company = Company(settings.issuer.cik10)
    filing = company.get_filings(form="10-K").head(1)[0]
    xbrl = filing.xbrl()

    facts_long = pd.read_parquet(staging_path)
    facts_by_concept = facts_long.drop_duplicates(subset=["concept"]).set_index("concept")
    companyfacts_units = facts_by_concept["unit"].to_dict()
    # period_type is a schema-level property of the XBRL element (instant vs duration),
    # so any observed fact for a concept tells us its type for every statement it appears on.
    companyfacts_period_types = facts_by_concept["period_type"].to_dict()
    companyfacts_concepts = set(facts_long["concept"].unique())

    face_rows_by_stmt: dict[str, pd.DataFrame] = {}
    all_excluded: list[dict] = []
    for prefix, _label, accessor in STATEMENTS:
        df = getattr(xbrl.statements, accessor)().to_dataframe()
        face, excluded = face_rows_for_statement(prefix, df)
        face_rows_by_stmt[prefix] = face
        all_excluded.extend(excluded)

    line_items, subtotal_checks = build_line_items_and_checks(
        face_rows_by_stmt, companyfacts_units, companyfacts_period_types
    )

    for li in line_items:
        if "concepts" in li:
            li["_in_companyfacts"] = li["concepts"][0] in companyfacts_concepts

    report_text = render_report(filing.accession_no, str(filing.filing_date), line_items, all_excluded, face_rows_by_stmt)

    for li in line_items:
        li.pop("_in_companyfacts", None)

    config_dir = pathlib.Path(config_dir)
    reports_dir = pathlib.Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)

    accn_nodash = filing.accession_no.replace("-", "")
    stamp = dt.date.today().strftime("%Y%m%d")
    report_path = reports_dir / f"concept_discovery_{accn_nodash}_{stamp}.md"
    report_path.write_text(report_text, encoding="utf-8")

    (config_dir / "line_items.yaml").write_text(
        yaml.dump(line_items, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8"
    )

    all_checks = FIXED_IDENTITY_CHECKS + subtotal_checks
    templates = {"checks": all_checks, "drivers": [], "model": []}
    (config_dir / "templates.yaml").write_text(
        yaml.dump(templates, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8"
    )

    return report_path
