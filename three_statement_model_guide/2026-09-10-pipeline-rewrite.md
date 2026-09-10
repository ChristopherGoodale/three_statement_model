# 2026-09-10 - Pipeline Rewrite

Supersedes the premise of the first setup entry (`2026-09-10-project-setup.md`): "the model is built by hand in Sheets, the repo just holds data" is no longer the plan. That entry's own closing note called this out as the point to revisit if the pattern repeated — it has, and the revisit is this rewrite.

## The "Why" (Design Decisions)

**The hand-built xlsx → Sheets port had exactly the failure mode a spreadsheet-of-facts approach invites.** `data/SiteOne_10K_FY2023-2025_FIXED.xlsx` and `scripts/import_to_sheets.py` (both now deleted) wrote plain values into the sheet with no enforced link back to the filing they came from, and their "checks" were static "✓" text in a notes column — text that can never turn red, because nothing recomputes it. A value landing in the wrong period or column had nothing to catch it. That's not a one-off mistake to fix; it's the structural weakness of "type numbers into a spreadsheet, then eyeball it."

**The new pipeline (`docs/SPEC_site_3sm_pipeline.md`, `PLAN.md`) makes provenance and validation structural instead of habitual.** Every value traces to a specific SEC accession number and raw filing document. Python validates every accounting identity (balance sheet balances, cash rolls forward, subtotals tie to reported values, etc.) *before* anything is written to the sheet. Checks render as live formulas (`=IF(ABS(diff)<=tolerance,"OK","CHECK")`), not text, so they actually fail visibly if a later edit breaks the linkage. The whole workbook is regenerable from scratch at any time — the sheet is an output of the pipeline, not a thing that's manually maintained.

## Core Concepts

- **`companyfacts` vs. instance-document parsing.** SEC's XBRL company-facts API only returns facts tagged with standard taxonomies (us-gaap, dei, etc.) that apply entity-wide. SITE uses some custom (extension) tags and dimensional facts (e.g., equity roll-forward components), which don't show up there — those require parsing the filing's actual XBRL instance document and linkbases instead. Phase 2's "discovery" step figures out which lines need which source.
- **Fact selection with fallback and recast handling.** A `line_id` (e.g. `IS_NET_SALES`) maps to an ordered list of candidate XBRL concepts. When the same fact is reported differently across filings (a recast), the most-recently-filed value wins, and the discarded value is still logged — never silently dropped.
- **Config requires owner approval before use.** `config/line_items.yaml` and `config/templates.yaml` start with every entry `status: proposed`; the pipeline refuses to use anything not `status: approved`. This is the mechanism that keeps a wrong XBRL-tag guess from silently becoming "the number."
- **Provenance-by-color.** Every cell's formatting is derived from what kind of cell it is, never hand-set: blue = sourced fact, black = same-tab formula, green = cross-tab formula, yellow fill = human-entered forecast input (left blank by code).
- **Scratch vs. production sheets.** The pipeline writes to a scratch sheet by default; writing to the production sheet requires an explicit `--promote` flag. The sheet that was previously hand-populated (`1Ptj78386ihOb4BKjSAffDqTz-hZPplJ6Lq9TgXID4po`) becomes `SHEET_ID_PROD` going forward, now cleared of its legacy tabs.

## Implementation Breakdown

As of this entry, no pipeline code exists — `src/site3sm/` holds only an empty `__init__.py`. `PLAN.md` covers Phase 0 (this scaffold) through Gate 0; `docs/SPEC_site_3sm_pipeline.md` is the full spec Phases 1–7 implement. See `PLAN.md` §5 for the open questions that gate Phase 1's start (validation tolerance, fiscal-year convention, cash-tie target, etc.) — several need the owner to check SITE's actual 10-K Note 1, which is why they're listed rather than guessed.

Future entries in this guide should track what each phase actually built once code starts landing — e.g. what the parser spike (Phase 2) concluded and why, how the balance sheet ends up balancing without a plug (Phase 7's linkage order), and how forecast-period interest is calculated on beginning-of-period debt specifically to avoid circularity.
