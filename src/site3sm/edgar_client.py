"""Throttled, cached, retrying HTTP client for SEC EDGAR (spec Sec 9.1).

R8: credentials/identifiers come from env only.
R9: declared User-Agent on every request, rate-limited, retried with backoff, disk-cached.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import time
from datetime import datetime, timezone

import requests


class MissingUserAgentError(RuntimeError):
    pass


class SecRequestError(RuntimeError):
    pass


class RateLimiter:
    """Simple token-bucket-ish limiter: never issue more than `rate` calls/sec.

    Clock/sleep are injectable so tests can verify behavior without real waits.
    """

    def __init__(self, rate: float, time_func=time.monotonic, sleep_func=time.sleep):
        self.min_interval = 1.0 / rate if rate > 0 else 0.0
        self._time = time_func
        self._sleep = sleep_func
        self._last_call: float | None = None

    def wait(self) -> None:
        now = self._time()
        if self._last_call is not None:
            elapsed = now - self._last_call
            remaining = self.min_interval - elapsed
            if remaining > 0:
                self._sleep(remaining)
        self._last_call = self._time()


class EdgarClient:
    RETRY_STATUS_CODES = {429, 500, 502, 503, 504}

    def __init__(
        self,
        cache_dir: str | pathlib.Path = "data/raw",
        user_agent: str | None = None,
        max_requests_per_second: float = 8,
        max_retries: int = 5,
        backoff_base_seconds: float = 1.0,
        refresh: bool = False,
        session: requests.Session | None = None,
        rate_limiter: RateLimiter | None = None,
        sleep_func=time.sleep,
    ):
        self.user_agent = user_agent if user_agent is not None else os.environ.get("SEC_USER_AGENT")
        if not self.user_agent:
            raise MissingUserAgentError(
                "SEC_USER_AGENT is not set. Export it (see .env.example) before making requests."
            )
        self.cache_dir = pathlib.Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self.refresh = refresh
        self.session = session or requests.Session()
        self.rate_limiter = rate_limiter or RateLimiter(max_requests_per_second)
        self._sleep = sleep_func

    def _cache_paths(self, url: str) -> tuple[pathlib.Path, pathlib.Path]:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.body", self.cache_dir / f"{digest}.meta.json"

    def _read_cache(self, url: str) -> tuple[bytes, dict] | None:
        body_path, meta_path = self._cache_paths(url)
        if not (body_path.exists() and meta_path.exists()):
            return None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return body_path.read_bytes(), meta

    def _write_cache(self, url: str, content: bytes, status: int, content_type: str | None) -> pathlib.Path:
        body_path, meta_path = self._cache_paths(url)
        body_path.write_bytes(content)
        meta = {
            "url": url,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "content_type": content_type,
        }
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return body_path

    def get(self, url: str) -> tuple[bytes, pathlib.Path]:
        """Return (content_bytes, raw_path). Uses the disk cache unless refresh=True."""
        if not self.refresh:
            cached = self._read_cache(url)
            if cached is not None:
                content, _meta = cached
                body_path, _ = self._cache_paths(url)
                return content, body_path

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self.rate_limiter.wait()
            try:
                response = self.session.get(url, headers={"User-Agent": self.user_agent}, timeout=30)
            except requests.RequestException as exc:
                last_exc = exc
                self._sleep(self.backoff_base_seconds * (2**attempt))
                continue

            if response.status_code in self.RETRY_STATUS_CODES and attempt < self.max_retries:
                self._sleep(self.backoff_base_seconds * (2**attempt))
                continue

            if response.status_code != 200:
                raise SecRequestError(f"{url} -> HTTP {response.status_code}")

            body_path = self._write_cache(
                url, response.content, response.status_code, response.headers.get("Content-Type")
            )
            return response.content, body_path

        raise SecRequestError(f"{url} failed after {self.max_retries + 1} attempts") from last_exc

    def get_json(self, url: str) -> tuple[dict, pathlib.Path]:
        content, raw_path = self.get(url)
        return json.loads(content), raw_path

    # -- SEC EDGAR endpoints (spec Sec 9.1) --

    def submissions(self, cik10: str) -> tuple[dict, pathlib.Path]:
        return self.get_json(f"https://data.sec.gov/submissions/CIK{cik10}.json")

    def companyfacts(self, cik10: str) -> tuple[dict, pathlib.Path]:
        return self.get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json")

    def filing_index(self, cik: int, accn_nodash: str) -> tuple[dict, pathlib.Path]:
        return self.get_json(f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn_nodash}/index.json")
