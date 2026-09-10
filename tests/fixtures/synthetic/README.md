# Synthetic fixtures

Fixtures in this directory are for unit tests only. Per `docs/SPEC_site_3sm_pipeline.md` §2 (R1 corollary):

- Must be obviously artificial — not a disguised or approximated version of SITE's actual statements.
- Must never be written to any Google Sheet.
- Integration tests use cached real SEC responses instead (`tests/fixtures/sec_cached/`), not these.
