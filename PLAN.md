# PLAN — SITE Three-Statement Model Pipeline

Status: **Phase 3 complete, awaiting Gate 3 approval.** Gates 0-2 cleared. See `docs/SPEC_site_3sm_pipeline.md` for the full spec this plan implements, and the guide entries in `three_statement_model_guide/` for what each phase actually built.

## 1. Objective (restated)

Build a reproducible Python pipeline that pulls SiteOne Landscape Supply's (NYSE: SITE) financials from official SEC EDGAR XBRL sources, selects exactly one authoritative, fully-sourced value per statement line per period, validates every accounting identity in Python before anything is rendered, and writes a Google Sheets workbook where every subtotal, check, driver, and forecast line is a live formula — never a pasted number. The workbook supports a three-week curriculum: verified historicals, forecast drivers, then linkage/balancing. Nothing is "done" until it is read back from the sheet and verified to match what was intended.

## 2. Phase breakdown

| Phase | Tasks | Files created/modified | Tests | Size |
|---|---|---|---|---|
| **0** (this plan) | Scaffold repo, retire legacy `data/SiteOne_10K_FY2023-2025_FIXED.xlsx` and `scripts/`, clear legacy sheet tabs, write this `PLAN.md` | `PLAN.md`, `README.md`, `pyproject.toml`, `requirements.txt`, `.env.example`, `.gitignore`, `docs/SPEC_site_3sm_pipeline.md`, empty `config/`/`data/{raw,staging,curated}`/`src/site3sm/`/`tests/`/`reports/`/`runs/`, guide entry | none (no code yet) | S |
| **1** | `pyproject.toml`/deps finalize, `config_models.py` (pydantic schemas for settings/line_items/templates), `edgar_client.py` (throttled, cached, retrying SEC client), filing discovery, `companyfacts` → `facts_long.parquet` | `src/site3sm/config_models.py`, `src/site3sm/edgar_client.py`, `src/site3sm/extract.py` (standard-facts path only), `config/settings.yaml`, `tests/unit/test_edgar_client.py` | throttle, backoff, cache hit/miss, missing-User-Agent failure — no network calls in unit tests | M |
| **2** | Parser spike (Arelle vs edgartools vs lxml) against the anchor filing; `discovery.py`; instance-document parsing for custom/dimensional facts; draft `config/line_items.yaml` (all `status: proposed`) and proposed `checks` in `config/templates.yaml` | `src/site3sm/discovery.py`, `config/line_items.yaml` (draft), `config/templates.yaml` (draft checks), `reports/concept_discovery_*.md` | parser selection documented, not unit-testable in the usual sense | L |
| **3** | `select_facts.py` (dedup, recast logging, fallback resolution, sign application), `validate.py` (11 identities from spec §9.5) — vertical slice IS → BS → CF → EQ | `src/site3sm/select_facts.py`, `src/site3sm/validate.py`, `tests/unit/test_select_facts.py`, `tests/unit/test_validate.py`, `data/curated/historicals.parquet` + `recasts.parquet` | duration filtering (357–378 day window), latest-filed-wins + recast log, missing-required raises, allow-missing yields no row, dimensional non-conflation, `fy`/`fp` never read, each identity pass/fail case, magnitude guard (the legacy-bug regression test) | L |
| **4a** | `layout.py` (period columns, header rows, row assignment, token resolver, collision guard), `formulas.py`, `sheets_writer.py`, `verify.py`; `Hist_IS` end-to-end (dry-run → scratch write → verify) | `src/site3sm/layout.py`, `src/site3sm/formulas.py`, `src/site3sm/sheets_writer.py`, `src/site3sm/verify.py`, `tests/unit/test_layout.py`, `tests/unit/test_formulas.py`, `tests/unit/test_sheets_writer.py` | column mapping stability, every template token form + every resolver error, collision guard (reproduces the legacy column-offset bug and raises), human-cell exclusion, dry-run determinism | L |
| **4b** | `Hist_BS`, `Hist_CF`, `Hist_EQ`, `Sources`, `README` tabs | same modules, extended coverage | verify report passes for all tabs | M |
| **5** | `Checks` tab — all identities from spec §10.2 as live formulas, `CHK_MASTER` | `templates.yaml` (approved checks) | read-back shows every status `OK` | M |
| **6** | `Assumptions` tab — historical driver formulas (§10.3) + blank yellow-fill forecast input row per driver | `templates.yaml` (approved drivers) | driver formulas render correctly, blank on undefined-prior-period columns | M |
| **7** | `Model` tab from approved templates + model checks; requires `fiscal_year_rule`, `forecast_years`, approved `model` templates | `templates.yaml` (approved model), `src/site3sm/run.py` (`build`/`verify` for Model) | `CHK_MODEL_BS_BALANCE`, `CHK_MODEL_CASH_TIE`, `CHK_INPUTS_COMPLETE`, `CHK_MASTER` all `OK` | L |
| **Promote** | `python -m site3sm.run all --promote` against `SHEET_ID_PROD` | — | production verify report | S |

Every phase's own gate report follows the template in spec §12.3 and stops for owner approval before the next phase starts (spec §0, §11).

## 3. Dependencies to add

| Dependency | Purpose |
|---|---|
| `requests` | SEC EDGAR HTTP client (submissions, companyfacts, filing index/documents) |
| `pandas` + `pyarrow` | `facts_long` / `historicals` / `recasts` tabular processing and parquet I/O |
| `pyyaml` | Load `config/*.yaml` |
| `pydantic>=2` | Validate all config at startup; abort on bad config |
| `gspread` + `google-auth` | Google Sheets read/write as the existing service account (`keys/personal_google_service_account.json`) |
| `python-dotenv` | Load `.env` for local runs |
| `pytest` | Unit + integration test suite |
| XBRL parser (Arelle, edgartools, or `lxml` direct) | Instance-document parsing for custom/dimensional facts — **selected at Gate 2** after the Phase 2 spike, not decided yet |

No dependency manager beyond plain `pip` + `venv` (matches every other Python project in this GitHub folder — see Phase 0 exploration notes; neither `uv` nor `poetry` is installed here or used elsewhere).

## 4. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Custom (extension) XBRL tags don't appear in `companyfacts` | Phase 2 instance-document parsing handles these explicitly; `line_items.yaml` entries for custom concepts are flagged and owner-approved before use (R2) |
| Dimensional equity facts (retained earnings roll-forward, RNCI roll-forward) aren't entity-wide `companyfacts` | Sourced from instance-document parsing of the Statement of Stockholders' Equity only, never assumed from other statements (spec §3) |
| Recasts — the same concept/period reported differently across filings | `recasts.parquet` logs every candidate value *before* dedup; resolution rule is "most recently filed wins," never silently averaged or picked arbitrarily |
| Restricted cash ambiguity in the CF cash definition | `cash_tie` line IDs are an explicit owner decision (settings, null until set) — pipeline refuses to validate the cash tie until it's set, rather than guessing |
| 52/53-week fiscal years | Duration filter uses an inclusive 357–378 day window (covers both 364- and 371-day years); never assumes Dec 31 year-ends or fixed 365-day periods |
| Google Sheets API quotas | Batched writes grouped by tab; `--dry-run` produces the full write-set as a CSV with zero API calls for iteration before any live write |
| Float precision in read-back comparison | `verify.py` asserts fact-cell equality within a defined float epsilon, not exact equality |
| **This repo already held the exact anti-pattern the spec forbids** — `data/SiteOne_10K_FY2023-2025_FIXED.xlsx` and `scripts/import_to_sheets.py` wrote plain pasted values and static "✓" check text, and the previously-shared Google Sheet held that same data | Both deleted from the repo in Phase 0 (this commit); legacy sheet tabs cleared (the sheet had in fact already been reset to blank by the owner before this phase ran). Neither is a data source or test oracle going forward (R11) |

## 5. Open questions for the owner

Per spec §15, these need the owner's judgment (several require reading SITE's actual 10-K Note 1) before the phases that depend on them can proceed:

1. ~~Dependency manager~~ — **resolved:** plain `pip` + `venv`, no uv/poetry.
2. ~~XBRL parser selection~~ — **resolved: edgartools.** Spiked directly against the anchor filing (see `three_statement_model_guide/2026-09-10-phase2-discovery.md`); met every Sec 9.3 acceptance criterion on the first attempt. Arelle not separately installed/tested (documented trade-off); direct lxml rejected as reinventing what edgartools already does. Pinned in `requirements.txt`.
3. ~~Validation tolerance~~ — **resolved:** `0.1` ($ millions), set in `config/settings.yaml`. Does not block Phase 1 (Phase 3).
4. ~~Include the opening balance sheet column~~ — **resolved: yes.** `periods.include_opening_balance_sheet: true` in `config/settings.yaml` — Phase 1's filing discovery must reach back one more fiscal year-end to cover it.
5. Cash-tie target — **still open** (`cash_tie.*` left `null` in settings, by design — set at Gate 2 from discovery). Does not block Phase 1.
6. Fiscal-year rule + forecast years — **still open** (`periods.fiscal_year_rule` / `forecast_years` left `null`). Blocks Phase 7 only, not Phase 1.
7. ~~Share count display scale~~ — **resolved:** millions, unrounded (`shares_display_scale: 1000000`). Does not block Phase 1.
8. ~~Scratch and production sheet IDs~~ — **resolved.** `SHEET_ID_PROD = 1Ptj78386ihOb4BKjSAffDqTz-hZPplJ6Lq9TgXID4po` (cleared), `SHEET_ID_SCRATCH = 1Cmm0ZxKPcHht-WXtlmJAyiIuJIxAEt0UDRlfr5mzRcE` (new, shared with the service account and confirmed reachable). Both in `.env` (gitignored). Neither blocks Phase 1 (Sheets writing starts at Phase 4).
9. Debt definition for the interest-rate driver — **still open.** Blocks Phase 6 (Assumptions drivers) only, not Phase 1.
10. Final driver list and forecast method per Model line — **still open**, approved before Phase 7.

**New setting added by the owner, not in the original spec (§9.5/§12.3 scope-control note):** `validation.report_all_nonzero_differences: true` in `config/settings.yaml` — `validate.py`'s report should list every check's numeric difference, not just the ones that fail tolerance. Noted here per R12 rather than silently absorbed.

**Remaining blockers for Phase 3 (select_facts.py/validate.py): item 5 (cash-tie target).** Everything else Phase 3 needs -- approved `line_items.yaml`/`templates.yaml` from this Gate 2 review, tolerance, opening-BS decision -- is set. Items 6, 9, 10 remain open but gate later phases (7, 6, 7 respectively).

## 6. Explicitly out of scope (v1)

10-Q ingestion, quarterly or LTM views, valuation (DCF, comps), charts, segment data, non-GAAP reconciliations, and scheduling/automation of runs. (Spec §4.)
