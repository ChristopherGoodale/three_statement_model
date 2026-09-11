# 2026-09-10 - Phase 4b: Rendering Hist_BS, Hist_CF, Hist_EQ, Sources, README

Builds on [Phase 4a](2026-09-10-phase4a-render-hist-is.md). Same machinery (`layout.py`/`formulas.py`/`sheets_writer.py`/`verify.py`), extended to the remaining historical tabs plus two flat (non-period-column) tabs. All six tabs now live in the scratch sheet, 0 failures across 7,299 checks.

## The "Why" (Design Decisions)

**Column E's eligibility is a per-TAB rule, exactly as spec Sec 9.6 states ("BS/EQ only; blank on IS/CF") — not something inferred per line.** Phase 4a's fix made this a per-*line* decision (based on each line's own `period_type`), which happened to work for `Hist_IS` but was actually the wrong generalization: `Hist_CF`'s beginning/ending-cash lines are legitimately instant-typed (for Phase 3 selection purposes) yet the spec still wants them rendered on CF's ordinary F/G/H axis, never E. A per-line rule would have pulled them onto a 4th column CF was never meant to have. Replaced with a hardcoded `E_ELIGIBLE_TABS = {"Hist_BS", "Hist_EQ"}`, matching the spec's own words instead of trying to infer the same fact from line metadata. Duration-typed lines on an E-eligible tab (e.g. `Hist_EQ`'s net-income-into-retained-earnings row) still end up blank at E on their own — there's simply no duration fact at the opening instant to find — so nothing extra was needed for that side.

**`Sources` and `README` don't fit the period-column model at all, so they get their own builders (`build_sources_write_set`, `build_readme_write_set`) rather than being forced through `build_write_set`.** `Sources` is a flat table — one row per curated fact, sorted deterministically — and `README` is eight lines of static-ish text. Trying to bend `TabLayout`'s row-per-line-item, column-per-period model to fit either would have made both harder to read than just writing the row/column logic directly.

**`README` is excluded from `verify`'s tab set, `Sources` is not.** `Sources` is a pure, deterministic function of `historicals.parquet` — re-running the same builder against the same data should always produce the same write set, so it verifies like any other tab. `README`'s "Last run ID" line is a timestamp snapshot of whatever build actually ran; recomputing it at verify time would generate a *different* run ID and fail every time for a reason that has nothing to do with correctness. Documented in code rather than silently skipped.

## Two more real bugs found via live testing

**`Hist_CF`'s beginning-cash *fact* (the earliest column) looked up the value at its own column's date instead of the prior instant.** `CF_BEGINNING` and `CF_ENDING` are the same underlying XBRL instant fact reused across two presentation rows (same subtlety Phase 3's `validate.py` hit and fixed for the Python-side checks) — so a naive "look up this line's curated value at this column's own `period_end`" returns the *ending* balance for that year, not the beginning one. First live write put $82.5M (FY2023's ending cash) in the "Beginning" row for FY2023 instead of $29.1M (the real beginning balance, matching the original filing exactly once fixed). Fixed by having the `fact_then_formula` kind's first-column case look up the instant *immediately prior* to its own column in the full (untrimmed) period axis, not the column's own date.

**New tabs were created with a fixed 200-row cap, and `Sources` needs 423.** `ensure_tab` sized every new worksheet at `rows=200, cols=26` regardless of what was about to be written into it — fine for the four `Hist_*` tabs (~50 rows each) but `Sources` (422 curated rows + a header) blew past it immediately: `Range (Sources!A201) exceeds grid limits`. Fixed by sizing (and resizing existing tabs, not just new ones) from the actual write set's own max row/column instead of a constant.

## Core Concepts

- **`kind: "fact_then_formula"`** is a new line-item kind (not in the original spec schema, added here because Hist_CF's beginning-cash line genuinely needs a different treatment per-cell, not per-line — spec Sec 10.1 describes exactly this: "beginning cash for FY-1 onward is a formula ...; FY-2 beginning cash is a fact"). Manually set on `CF_BEGINNING` in `config/line_items.yaml` after the automated discovery pass, since this is a rendering-time distinction discovery has no way to infer from the filing's own structure.
- **RNCI's presentation position** (between total liabilities and stockholders' equity) came through correctly in the live sheet exactly where spec Sec 3 requires, without any special-casing — it's just wherever edgartools' own presentation order put it back in Phase 2.

## Implementation Breakdown

- `sheets_writer.py`: `E_ELIGIBLE_TABS`, the `fact_then_formula` per-cell kind handling (including the prior-instant lookup fix), `build_sources_write_set()`, `build_readme_write_set()`, and dynamic tab sizing in `SheetsWriter.ensure_tab()`/`write()`.
- `config/line_items.yaml`: `CF_BEGINNING` manually patched to `kind: fact_then_formula` with `template: "{CF_ENDING@prev}"`.
- `run.py build`/`verify` extended to dispatch `Sources`/`README` to their own builders instead of the generic period-column path.
- Live result: all six tabs (`Hist_IS`, `Hist_BS`, `Hist_CF`, `Hist_EQ`, `Sources`, `README`) written to the scratch sheet. `verify` (excluding `README`): **0 failures across 7,299 checks.** RNCI sits between total liabilities and stockholders' equity as required; `Hist_CF` beginning cash for FY2023 now reads $29.1M (matching the original filing) instead of the earlier-write's incorrect $82.5M.
