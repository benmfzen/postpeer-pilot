"""Reliability behaviour: retry policy, permanent-error short-circuit, idempotent re-runs."""
import pytest

from postpeer_pilot import api, scheduler


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(api.time, "sleep", lambda s: None)


def _runner(script):
    """script: list of (curl_exit, body, status) tuples consumed per call."""
    calls = {"n": 0}

    def run(cmd, timeout):
        code, body, status = script[min(calls["n"], len(script) - 1)]
        calls["n"] += 1
        return code, f"{body}\n{status}"
    return run, calls


def test_retryable_5xx_then_success(monkeypatch, no_sleep):
    run, calls = _runner([(0, '{"oops": true}', "503"), (0, '{"success": true}', "200")])
    monkeypatch.setattr(api, "_run", run)
    monkeypatch.setattr(api.config, "api_key", lambda: "k")
    assert api._req("GET", "/posts")["success"] is True
    assert calls["n"] == 2                              # exactly one retry


def test_permanent_4xx_fails_fast(monkeypatch, no_sleep):
    run, calls = _runner([(0, '{"error": "bad request"}', "400")])
    monkeypatch.setattr(api, "_run", run)
    monkeypatch.setattr(api.config, "api_key", lambda: "k")
    with pytest.raises(api.ApiError):
        api._req("POST", "/posts", {})
    assert calls["n"] == 1                              # no retry on permanent errors


def test_429_is_retried(monkeypatch, no_sleep):
    run, calls = _runner([(0, "{}", "429"), (0, "{}", "429"), (0, '{"ok": 1}', "200")])
    monkeypatch.setattr(api, "_run", run)
    monkeypatch.setattr(api.config, "api_key", lambda: "k")
    assert api._req("GET", "/posts")["ok"] == 1
    assert calls["n"] == 3


def test_network_error_exhausts_retries(monkeypatch, no_sleep):
    run, calls = _runner([(7, "curl: connection refused", "")])
    monkeypatch.setattr(api, "_run", run)
    monkeypatch.setattr(api.config, "api_key", lambda: "k")
    with pytest.raises(RuntimeError, match="after 3 attempts"):
        api._req("GET", "/posts")
    assert calls["n"] == 3


def test_i9_rerun_skips_already_scheduled(home, fake_api, video):
    """Idempotency: retrying a batch never double-posts the already-scheduled part."""
    from datetime import date, timedelta
    start = date.today() + timedelta(days=30)
    v = video("idem")
    r1 = scheduler.schedule([v], start=start)
    assert r1[0]["ok"]
    r2 = scheduler.schedule([v], start=start)
    assert r2[0]["ok"] is False and r2[0]["skipped"]
    assert fake_api.creates == 1                        # second run created nothing
    r3 = scheduler.schedule([v], start=start, allow_duplicate=True)
    assert r3[0]["ok"]                                  # explicit override still works
