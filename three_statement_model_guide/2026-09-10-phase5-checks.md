# 2026-09-10 - Phase 5: The Checks Tab

Builds on Phase 4. Covers `checks.py` and one bug in this phase that's worth reading closely — it's the exact failure mode the whole pipeline was built to prevent, and it happened anyway on the first pass.

## The "Why" (Design Decisions)

**`CHK_SUBTOTAL_<LINE_ID>` is not rendered as a live sheet check.** Spec §10.2 lists it as "formula value minus reported value," but under the single-entry rule (§10.1: every fact written exactly once, on its home tab) a formula-kind line's cell *is* the only place its value exists anywhere on the sheet — there's no separately-written "reported" value left to diff it against live. That comparison already happened, rigorously, in Python during Phase 3 (`validate.py`, against the true curated/reported value) and is recorded in `reports/validation_*.md`. A sheet-level check that compared a cell to itself would be tautological, not a check — so it's deliberately omitted, with the reasoning in `checks.py`'s module docstring rather than silently dropped.

**`CF_BEGINNING` on `Hist_CF` (not a separate lookup) is what a check formula references for "beginning cash."** `CHK_CF_CASH_ROLL`'s template is just `{Hist_CF!CF_BEGINNING} + {Hist_CF!CF_NET_CHANGE_IN_CASH} - {Hist_CF!CF_ENDING}` — no special-casing needed here, because Phase 4b already taught `Hist_CF!CF_BEGINNING` to correctly be a fact for the earliest column and a formula for the rest. The abstraction earns its keep: a check written one level up doesn't need to know that distinction exists.

**Which columns a check is written for is a per-check judgment call, not something inferred from the resolver.** `resolve_formula`'s `@prev` only raises when there's *no earlier column at all* — it has no way to know whether the referenced tab actually wrote real data at that earlier column. `CHK_RE_ROLLFORWARD`/`CHK_RNCI_ROLLFORWARD` reference `Hist_EQ` (which does write column E), so `@prev` from F correctly lands on real data. `CHK_CF_CASH_CONTINUITY` references `Hist_CF` (which never writes column E — spec §9.6 says E is "BS/EQ only"), so its `@prev` from F would resolve *syntactically* fine while pointing at a permanently blank cell. Column F is excluded for that one check explicitly (`duration_cols[1:]`), not caught by exception handling — the exception clause is documented as belt-and-suspenders, not the real mechanism.

## Two real bugs found via live testing — the second one is the important one

**`@abs` resolved to the *calling* column instead of the scalar's own fixed location.** `CHK_TOLERANCE` is one value; every period's status-row formula needs to reference the *same* cell. The original `@abs` implementation returned `${col}${row}` using whichever column happened to be asking — so a formula in column G would get `$G$7` instead of `$F$7`, pointing at a blank cell instead of the real tolerance value. Fixed by giving scalars one fixed column (`SCALAR_COL = "F"`, in `layout.py`) that every `@abs` reference resolves to regardless of caller.

**The tolerance itself was in the wrong units — and `CHK_MASTER` said "OK" anyway.** `Hist_*` cells hold values already scaled to $ millions (`455.5`, not `455500000`). `CHK_TOLERANCE` was written as the *raw unscaled* dollar tolerance (`0.1 * 1,000,000 = 100,000`, matching what Python's `validate.py` needs against unscaled curated data) — comparing a ~$10-scale diff against a 100,000-scale threshold, which is trivially true no matter what the real discrepancy is. First live write of the Checks tab showed `CHK_MASTER = "OK"` while `CHK_BS_BALANCE` was visibly showing non-zero diffs ($7.8M, $10.3M — the already-known Phase 3 dropped-line gap) right next to it. That combination — a real, known discrepancy sitting next to a check that says everything's fine — is *exactly* the pattern this whole pipeline exists to catch (R1's magnitude-guard concept, one level up: not a fabricated value, but a check rendered meaningless by a scale mismatch). Caught by reading the live sheet back and noticing the two didn't agree, not by any automated test — worth remembering that live inspection still matters even with a thorough test suite. Fixed by using `settings.validation.tolerance_usd_millions` directly (already in the right scale) instead of multiplying by `usd_display_scale`.

After the fix: `CHK_MASTER` correctly reads `FAILURES: 2`, matching the two periods (FY2022 opening, FY2023) where the dropped-line gap from Phase 3 actually shows up. `CHK_BS_BALANCE` reads `CHECK` at exactly those two columns and `OK` everywhere else.

**A third, more mundane finding:** Sheets silently drops single-quotes around a sheet name that doesn't need them (no spaces/special characters) when it stores a formula — `='Hist_BS'!E21` round-trips as `=Hist_BS!E21`. Not a correctness bug (the formula evaluates identically), but `verify.py`'s exact-string formula comparison was too strict for it. Fixed with a normalization step (strip all single-quotes before comparing — safe, since none of our formulas use one for anything else; string literals use double quotes).

## Core Concepts

- **`kind: "check_diff"` / `"check_status"`** are new synthetic kinds, used only within `checks.py`'s own `TabLayout` construction — the Checks tab's rows aren't statement facts, so they don't belong in `config/line_items.yaml`, but the same row-assignment machinery (unique sequential rows, no collisions) applies unchanged.
- **`CHK_MASTER`'s formula is generated, not hand-written**: it walks every `check_status` row actually present in the layout (via `TabLayout.kind_of`) and builds one `COUNTIF(...,"CHECK")` term per row, summed. Adding or removing a check changes `CHK_MASTER`'s formula automatically — nothing to keep in sync by hand.

## Implementation Breakdown

- `checks.py`: `IDENTITY_CHECKS` (the 6 checks written at every applicable period), `CF_CASH_CONTINUITY` (the one exception needing an explicit narrower column list), `build_checks_layout()`, `build_checks_write_set()`.
- `layout.py`: added `SCALAR_COL = "F"`.
- `formulas.py`: `@abs` now resolves to `SCALAR_COL`, not the calling column.
- `verify.py`: `_normalize_formula()` strips quoting noise before comparing formula text.
- `run.py`: `_cash_tie_config()` helper; `Checks` dispatched in both `build` and `verify`.
- Live result: `CHK_MASTER` = `FAILURES: 2`, correctly and honestly reflecting the two periods affected by the already-documented Phase 3 dropped-line gap. Every other check across every period: `OK`. `verify`: 0 failures across 7,395 checks.
