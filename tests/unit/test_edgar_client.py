import json

import pytest

from site3sm.edgar_client import EdgarClient, MissingUserAgentError, RateLimiter, SecRequestError


class FakeResponse:
    def __init__(self, status_code=200, content=b"{}", content_type="application/json"):
        self.status_code = status_code
        self.content = content
        self.headers = {"Content-Type": content_type}


class FakeSession:
    """Records calls; returns responses from a preset queue (one per call)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        if not self._responses:
            raise AssertionError("FakeSession ran out of queued responses")
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


def test_missing_user_agent_fails(monkeypatch, tmp_path):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(MissingUserAgentError):
        EdgarClient(cache_dir=tmp_path, user_agent=None)


def test_cache_hit_no_network(tmp_path):
    session = FakeSession([FakeResponse(content=json.dumps({"a": 1}).encode())])
    client = EdgarClient(cache_dir=tmp_path, user_agent="test test@example.com", session=session, sleep_func=lambda s: None)

    data1, _ = client.get_json("https://data.sec.gov/fake.json")
    assert data1 == {"a": 1}
    assert len(session.calls) == 1

    data2, _ = client.get_json("https://data.sec.gov/fake.json")
    assert data2 == {"a": 1}
    assert len(session.calls) == 1  # served from cache, no second network call


def test_refresh_bypasses_cache(tmp_path):
    session = FakeSession(
        [
            FakeResponse(content=json.dumps({"a": 1}).encode()),
            FakeResponse(content=json.dumps({"a": 2}).encode()),
        ]
    )
    client = EdgarClient(
        cache_dir=tmp_path, user_agent="test test@example.com", session=session, refresh=True, sleep_func=lambda s: None
    )
    data1, _ = client.get_json("https://data.sec.gov/fake.json")
    data2, _ = client.get_json("https://data.sec.gov/fake.json")
    assert data1 == {"a": 1}
    assert data2 == {"a": 2}
    assert len(session.calls) == 2


def test_retries_on_5xx_then_succeeds(tmp_path):
    sleeps = []
    session = FakeSession(
        [
            FakeResponse(status_code=503, content=b""),
            FakeResponse(status_code=503, content=b""),
            FakeResponse(status_code=200, content=json.dumps({"ok": True}).encode()),
        ]
    )
    client = EdgarClient(
        cache_dir=tmp_path,
        user_agent="test test@example.com",
        session=session,
        max_requests_per_second=1_000_000,
        max_retries=5,
        backoff_base_seconds=0.01,
        sleep_func=sleeps.append,
    )
    data, _ = client.get_json("https://data.sec.gov/fake.json")
    assert data == {"ok": True}
    assert len(session.calls) == 3
    assert len(sleeps) == 2  # one backoff sleep per retried attempt


def test_fails_after_max_retries(tmp_path):
    session = FakeSession([FakeResponse(status_code=503, content=b"")] * 6)
    client = EdgarClient(
        cache_dir=tmp_path,
        user_agent="test test@example.com",
        session=session,
        max_requests_per_second=1_000_000,
        max_retries=3,
        backoff_base_seconds=0.01,
        sleep_func=lambda s: None,
    )
    with pytest.raises(SecRequestError):
        client.get_json("https://data.sec.gov/fake.json")
    assert len(session.calls) == 4  # initial attempt + 3 retries


def test_non_retryable_error_raises_immediately(tmp_path):
    session = FakeSession([FakeResponse(status_code=404, content=b"")])
    client = EdgarClient(
        cache_dir=tmp_path, user_agent="test test@example.com", session=session, sleep_func=lambda s: None
    )
    with pytest.raises(SecRequestError):
        client.get_json("https://data.sec.gov/fake.json")
    assert len(session.calls) == 1


def test_rate_limiter_sleeps_to_respect_rate():
    clock = {"t": 0.0}
    sleeps = []

    def time_func():
        return clock["t"]

    def sleep_func(seconds):
        sleeps.append(seconds)
        clock["t"] += seconds

    limiter = RateLimiter(rate=2, time_func=time_func, sleep_func=sleep_func)  # 0.5s min interval
    limiter.wait()  # first call: no wait
    clock["t"] += 0.1  # only 0.1s elapsed before next call
    limiter.wait()  # must sleep ~0.4s to reach the 0.5s interval

    assert sleeps == [pytest.approx(0.4)]


def test_rate_limiter_no_sleep_when_interval_already_elapsed():
    clock = {"t": 0.0}
    sleeps = []

    limiter = RateLimiter(rate=2, time_func=lambda: clock["t"], sleep_func=sleeps.append)
    limiter.wait()
    clock["t"] += 10.0  # plenty of time has passed
    limiter.wait()

    assert sleeps == []
