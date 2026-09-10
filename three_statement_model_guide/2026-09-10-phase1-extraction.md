# 2026-09-10 - Phase 1: SEC EDGAR Extraction

Builds on [`2026-09-10-pipeline-rewrite.md`](2026-09-10-pipeline-rewrite.md) — this entry covers what Phase 1 actually implemented: `config_models.py`, `edgar_client.py`, `extract.py`, `run.py extract`.

## The "Why" (Design Decisions)

**Rate limiting is enforced twice, at two different layers, on purpose.** `SecConfig.max_requests_per_second` in `config_models.py` has a hard pydantic ceiling (`le=10`) — a config file literally cannot request a rate SEC would reject. Separately, `EdgarClient`'s `RateLimiter` enforces whatever rate *is* configured at request time. The config-layer cap exists so a bad settings.yaml value fails immediately and loudly at startup, rather than surfacing later as SEC throttling/blocking mid-run.

**The disk cache key is a hash of the URL, not the endpoint semantics.** Simpler than trying to encode "this is a companyfacts request for CIK X" into the filename, and it composes for free with any future endpoint (filing index, instance documents in Phase 2) without new cache logic.

**`extract.py` does not filter by form or by concept.** It would be tempting to only keep 10-K/10-K/A facts here, since that's ultimately what the model wants — but the spec assigns that filtering to `select_facts.py` (Phase 3) specifically so `facts_long` stays a faithful, complete flatten of whatever companyfacts actually returned. Filtering early would make it impossible to later audit *why* a value was excluded.

## Core Concepts

- **Token-bucket rate limiting with injectable clock/sleep.** `RateLimiter` takes `time_func`/`sleep_func` as constructor args so tests can verify wait behavior (e.g. "0.1s elapsed, rate is 2/sec, so sleep ~0.4s") without any real elapsed time.
- **Two-tier retry:** `RETRY_STATUS_CODES = {429, 500, 502, 503, 504}` retries with exponential backoff (`backoff_base_seconds * 2**attempt`); anything else non-200 fails immediately as `SecRequestError` rather than burning through retries on an error that won't resolve by waiting (e.g. a 404).
- **`companyfacts` aggregates across every filing already.** One API call per CIK returns every historical value the company has ever reported for every standard-taxonomy concept, each tagged with the specific filing (`accn`) that reported it. This is why Phase 1's "filing discovery" only needs the submissions API for the audit trail (which 10-Ks exist, for the run manifest) — it doesn't need to fetch each filing's own documents to get historical values; that only becomes necessary in Phase 2 for custom/dimensional facts that *aren't* in companyfacts.

## Implementation Breakdown

- `config_models.py` — pydantic v2 models mirroring `config/settings.yaml` 1:1, `extra="forbid"` on every model so a typo'd key fails loudly instead of being silently ignored.
- `edgar_client.py` — `EdgarClient.get()` is the single chokepoint: cache check → rate limit → request with retry/backoff → cache write. `submissions()`/`companyfacts()`/`filing_index()` are thin wrappers over `get_json()` for the three endpoints spec §9.1 names.
- `extract.py` — `discover_filings()` (submissions → 10-K/10-K/A list) and `normalize_companyfacts()` (flatten to the `facts_long` schema, spec §7.1) are separately unit-testable from `run_extract()` (the orchestrator that also asserts the Phase 1 acceptance criteria and writes the parquet + summary report).
- `run.py` — `extract` is fully wired; every other subcommand (`discover`, `select`, `validate`, `build`, `verify`, `all`) is registered in the CLI shape spec §9.9 defines, but raises `NotImplementedError` naming its phase, so the interface exists without pretending unbuilt phases work.

**Verified against live SEC EDGAR** (not just synthetic fixtures): 15,685 facts extracted for CIK 0001650729, cached to `data/raw/` (2 files: submissions + companyfacts, each with a body + metadata sidecar). A second run completed in ~1.8s with zero network calls, confirming the cache actually short-circuits re-fetching. Spot-checked `us-gaap:CashAndCashEquivalentsAtCarryingValue` at FY2025/FY2024 year-end against the earlier (now-deleted) hand-built xlsx — $190.6M / $107.1M, matches exactly, which is a good sign the earlier manual work was numerically accurate even though its provenance/check mechanism wasn't trustworthy.
