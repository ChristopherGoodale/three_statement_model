# 2026-09-10 - Phase 4a: Rendering Hist_IS

Builds on Phases 1-3. Covers `layout.py`, `formulas.py`, `sheets_writer.py`, `verify.py`, and the first tab actually written to a live sheet. This is where the pipeline stops being pure data work and starts producing the thing the user actually looks at -- still not the forecasting "meat" (that's Phase 6-7), but the first real UI surface.

## The "Why" (Design Decisions)

**One shared period-column map for every tab, keyed by instant date.** The spec's column axis (E=opening, F/G/H=the 3 historical years) has to mean the same thing on every tab so cross-tab formulas (`{Hist_BS!BS_CASH}`) land in the right column automatically. Since a duration fact's end date and its corresponding balance-sheet instant date are the same calendar date, one map built off the instant dates works for both instant and duration tabs -- no separate duration-axis bookkeeping needed.

**Formatting is derived from cell kind, computed once, never hand-set** -- literally can't set a font color without going through `build_write_set`'s kind classification (fact/same_tab_formula/cross_tab_formula/header/human_input), which is what spec Sec 9.7 requires ("never assign manually") and is also just the sane way to keep 100+ cells visually consistent.

**Human-owned cells are invisible to the regular write pipeline, not written-then-protected.** `build_write_set` skips a `owner: "human"` line's data cells entirely rather than writing a blank placeholder and hoping later code never overwrites it. Not exercised yet (Phase 6 is the first tab with human cells) but the exclusion is tested now (`test_human_owned_cells_excluded_from_write_set`) so Phase 6 starts from a known-correct base rather than retrofitting the safety property later.

## Two real bugs found via live testing (not just unit tests)

**Column E leaked into a duration-only tab.** `period_columns` is a single global map covering all 4 historical dates (opening + 3 years) once `include_opening_balance_sheet` is on. First live write iterated that whole map for every tab, including `Hist_IS` -- which has no data for the opening year at all. Result: 5 header cells and 5 formula cells silently written into column E, and since the formulas' inputs (blank fact cells) read as 0 in Sheets, `Gross Profit` etc. showed `0` in a column that shouldn't have existed on this tab in the first place. Fixed by deciding per-line (not just per-tab, since Hist_EQ genuinely mixes instant and duration lines) whether column E applies, based on each line's own `period_type`. Live-verified the fix by reading the actual sheet back and confirming column E is blank across the tab.

**Sheets returns dates as serial numbers, not ISO strings, under `UNFORMATTED_VALUE`.** `verify.py`'s first pass compared a written `datetime.date` against the read-back value using `.isoformat()` string equality -- which works for how the *value was sent*, but Sheets always reports a date cell back as a day-count serial (epoch 1899-12-30, the Excel/Sheets date system) regardless of what was written. All 6 header-row date checks failed for a reason that had nothing to do with the underlying data being wrong. Fixed with `_serial_to_date()` (unit tested directly, since it's a pure function independent of any live API call).

## Core Concepts

- **Row assignment is enumerate-based, not order-value-based.** `TabLayout` sorts approved line_items by their config `order` field, then assigns rows 7, 8, 9, ... sequentially -- the actual `order` values (100, 110, 300, ...) only ever determine sequence, never row numbers directly. This is what makes "two lines can never share a row" trivially true by construction rather than something that has to be separately validated.
- **The token resolver (`formulas.py`) is a pure function of (template, current tab, current column, all tab layouts)** -- no Sheets API involved. That's what makes every resolver error case (unknown line_id, unknown tab, `@prev` with no prior period, `@abs` on a non-scalar, unresolved braces) unit-testable without touching a live sheet at all.
- **The collision guard lives in `build_write_set`'s `put()` helper**, not as a post-hoc check -- a duplicate `(tab, a1)` target raises the instant it would happen, before any cell after it is even considered.

## Implementation Breakdown

- `layout.py`: `build_period_columns()`, `TabLayout` (row assignment + `row_of`/`kind_of` lookups), `prev_column()`.
- `formulas.py`: `resolve_formula()` (the full token grammar), `cell_kind()` (same_tab vs cross_tab, for font color).
- `sheets_writer.py`: `build_write_set()` (the per-tab bug above lives here), `write_dry_run()` (CSV), `SheetsWriter` (gspread-based live write: create-if-missing tabs, batch value write with `USER_ENTERED`, per-cell formatting via `repeatCell`, freeze header rows/columns A-D).
- `verify.py`: `verify_tab()` (the serial-date bug lives here), `render_report()`, `run_verify()`. A tab that hasn't been built yet is reported as a note (`tab not found`), not a failure -- `verify` running before every tab exists is expected during phased rollout, not an error condition.
- `run.py build --tabs Hist_IS [--dry-run] [--promote]` and `run.py verify [--promote]` wired up.
- Live result: dry-run (119 cells) → live write to scratch → verify, 0 failures across 140 checks (values, formula text, no-unexpected-cells). Gross Profit/Operating Income/Income Before Taxes/Net Income all confirmed as live formulas computing the correct figures directly in the sheet, not pasted values.
