# 2026-10-14 - Portable credentials path

## The "Why" (Design Decisions)

**The absolute path broke on the second machine.** `.env` held `GOOGLE_APPLICATION_CREDENTIALS=C:\Users\Chris\OneDrive\Documents\GitHub\keys\...`. On the Mac the same file lives at `/Users/cg/GitHub/keys/...`, so every Sheets call would fail with a missing-file error.

**Relative to the repo, not to the shell.** A relative path in `.env` is resolved against `REPO_ROOT`, not the working directory. `python -m site3sm.run` then behaves the same whether it is launched from the repo root, `src/`, or an IDE run configuration.

**Default to the shared `keys/` folder.** Every machine keeps `keys/` next to the repos (one level above `REPO_ROOT`). If the variable is unset, the pipeline falls back to `../keys/personal_google_service_account.json`. This matches `sheets_drive_mcp/index.js`, which already defaults to `../keys` with an env override.

**Absolute paths still win.** An absolute value in `.env` or the shell is used as is, so a CI runner or a key stored elsewhere still works.

## Core Concepts

- **Resolve once at startup.** `main()` normalizes the variable right after `load_dotenv`, so `cmd_build` and `cmd_verify` keep reading `os.environ["GOOGLE_APPLICATION_CREDENTIALS"]` unchanged and always get an absolute path.

## Implementation Breakdown

- `src/site3sm/run.py`: `_resolve_credentials_path()` reads the variable, applies the `../keys` default, joins relative values onto `REPO_ROOT`, and writes the absolute result back to `os.environ`.
- `.env.example`: shows the relative form, `GOOGLE_APPLICATION_CREDENTIALS=../keys/personal_google_service_account.json`.
- Local `.env` (gitignored): switched to the same relative value.
