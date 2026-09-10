# Three-Statement Financial Model — SiteOne Landscape Supply (SITE)

A reproducible Python pipeline that pulls SiteOne Landscape Supply, Inc. (NYSE: SITE, CIK 0001650729) financials straight from SEC EDGAR XBRL, validates every accounting identity in Python, and renders a Google Sheets workbook where every subtotal, check, driver, and forecast line is a live formula with full provenance back to the filing. See [finance_skills_projects.md](../finance_skills_projects.md) (project #1).

Full spec: [docs/SPEC_site_3sm_pipeline.md](docs/SPEC_site_3sm_pipeline.md). Current status and phase plan: [PLAN.md](PLAN.md).

## Structure

- `docs/` — the pipeline spec
- `config/` — line-item concept mappings and formula templates (owner-approved before use; empty until Phase 2)
- `data/raw/` — cached SEC responses (gitignored)
- `data/staging/`, `data/curated/` — intermediate and final parquet outputs (gitignored)
- `src/site3sm/` — the pipeline package
- `tests/` — unit tests (synthetic fixtures only) and integration tests (cached real SEC responses)
- `reports/`, `runs/` — generated run reports and manifests (gitignored)
- `three_statement_model_guide/` — companion learning guide documenting design decisions as the pipeline is built

## Status

Phase 0 (repo scaffold) is complete. See `PLAN.md` for the phase-by-phase build order and the open questions that need answering before Phase 1 (SEC data extraction) starts.

**Sheets:** production = the original model sheet, now cleared of its legacy (hand-ported) tabs; scratch = _(pending — service accounts can't create their own Sheets, so this needs to be created and shared manually)_.
