"""`invagent.datafeed.http` + `ratelimit` — 호스트 요청 제한을 피하는 재시도·간격·차단."""

import urllib.error

import pytest

from invagent.datafeed import cache, http, ratelimit


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "http")


class _Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _scripted(monkeypatch, outcomes):
    """`read_url`이 outcomes를 차례로 내놓게 한다. 예외면 던지고 bytes면 돌려준다."""
    calls = []

    def fake(url, headers, timeout):
        outcome = outcomes[len(calls)]
        calls.append(url)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(http, "read_url", fake)
    return calls


def _http_error(code, headers=None):
    return urllib.error.HTTPError("https://example.test/x", code, "err", headers or {}, None)


def test_a_rate_limited_request_is_retried_until_it_succeeds(monkeypatch) -> None:
    calls = _scripted(monkeypatch, [_http_error(429), b'{"ok": 1}'])

    payload, err = http.get_json("https://example.test/x", authed=False)

    assert (payload, err) == ({"ok": 1}, None)
    assert len(calls) == 2


def _record_sleeps(monkeypatch):
    """잠든 시간을 기록하고, 잠든 만큼 가짜 시계를 앞으로 돌린다."""
    slept = []
    clock = _Clock(1_000_000.0)

    def sleep(seconds):
        slept.append(seconds)
        clock.now += seconds

    monkeypatch.setattr(ratelimit, "_now", clock)
    monkeypatch.setattr(ratelimit, "_sleep", sleep)
    monkeypatch.setattr(ratelimit, "_jitter", lambda: 0.0)
    return slept


def test_retries_back_off_exponentially(monkeypatch) -> None:
    slept = _record_sleeps(monkeypatch)
    _scripted(monkeypatch, [ConnectionResetError("reset"), ConnectionResetError("reset"), b"{}"])

    http.get_json("https://example.test/x", authed=False)

    assert slept == [1.0, 2.0]


def test_a_retry_after_header_sets_the_wait(monkeypatch) -> None:
    slept = _record_sleeps(monkeypatch)
    _scripted(monkeypatch, [_http_error(429, {"Retry-After": "7"}), b"{}"])

    http.get_json("https://example.test/x", authed=False)

    assert slept == [7.0]


def test_an_unauthorized_response_is_not_retried(monkeypatch) -> None:
    calls = _scripted(monkeypatch, [_http_error(401), b"{}"])

    payload, err = http.get_json("https://example.test/x", authed=True)

    assert (payload, err, len(calls)) == (None, "HTTP 401", 1)


def test_exhausted_retries_cool_the_host_down_for_every_url(monkeypatch) -> None:
    """계속 두드리면 차단이 길어진다 — 한도에 걸린 호스트는 잠시 아무도 부르지 않는다."""
    clock = _Clock(1_000_000.0)
    monkeypatch.setattr(ratelimit, "_now", clock)
    calls = _scripted(monkeypatch, [_http_error(429)] * ratelimit.MAX_ATTEMPTS + [b'{"ok": 1}'])

    first = http.get_json("https://example.test/a", authed=False)
    second = http.get_json("https://example.test/b", authed=False)

    assert first == (None, "HTTP 429")
    assert second[0] is None and "요청 제한" in second[1]
    assert len(calls) == ratelimit.MAX_ATTEMPTS, "cool-down 중에는 네트워크를 타지 않는다"

    clock.now += ratelimit.COOLDOWN_SECONDS + 1
    assert http.get_json("https://example.test/b", authed=False) == ({"ok": 1}, None)


def test_a_cool_down_on_one_host_leaves_other_hosts_alone(monkeypatch) -> None:
    _scripted(monkeypatch, [_http_error(429)] * ratelimit.MAX_ATTEMPTS + [b'{"ok": 1}'])

    http.get_json("https://limited.test/a", authed=False)

    assert http.get_json("https://other.test/a", authed=False) == ({"ok": 1}, None)


def test_a_non_retryable_failure_does_not_cool_the_host_down(monkeypatch) -> None:
    _scripted(monkeypatch, [_http_error(401), b'{"ok": 1}'])

    http.get_json("https://example.test/a", authed=True)

    assert http.get_json("https://example.test/b", authed=True) == ({"ok": 1}, None)


def test_back_to_back_requests_to_one_host_are_spaced(monkeypatch) -> None:
    slept = _record_sleeps(monkeypatch)
    _scripted(monkeypatch, [b"{}", b"{}", b"{}"])

    http.get_json("https://example.test/a", authed=False)
    http.get_json("https://example.test/b", authed=False)
    http.get_json("https://other.test/a", authed=False)

    assert slept == [ratelimit.MIN_INTERVAL_SECONDS], "같은 호스트 두 번째만 기다린다"


def test_cache_hits_do_not_wait_for_the_spacing(monkeypatch) -> None:
    slept = _record_sleeps(monkeypatch)
    _scripted(monkeypatch, [b"{}"])

    http.get_json("https://example.test/a", authed=False)
    http.get_json("https://example.test/a", authed=False)

    assert slept == []


def test_concurrent_requests_to_one_host_are_capped(monkeypatch) -> None:
    """서브에이전트가 몇 개 떠도 한 호스트가 받는 동시 요청 수는 상한을 넘지 않는다."""
    import threading
    import time

    lock = threading.Lock()
    active = [0]
    peak = [0]

    def slow(url, headers, timeout):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        return b"{}"

    monkeypatch.setattr(http, "read_url", slow)
    threads = [
        threading.Thread(target=http.get_json, args=(f"https://example.test/{i}",), kwargs={"authed": False})
        for i in range(6)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert peak[0] == ratelimit.MAX_CONCURRENT_PER_HOST


def test_a_rate_limited_url_falls_back_to_its_last_response_with_a_label(monkeypatch, capsys) -> None:
    """제한에 걸렸을 때 보관 중인 지난 응답이 있으면 그것을 쓰되, 몇 분 전 값인지 밝힌다."""
    import os
    import time

    url = "https://example.test/stale"
    cache.store(url, b'{"price": 100}', authed=False)
    old = time.time() - 3600
    os.utime(cache.cache_path(url, authed=False), (old, old))
    _scripted(monkeypatch, [_http_error(429)] * ratelimit.MAX_ATTEMPTS)

    payload, err = http.get_json(url, authed=False)

    assert (payload, err) == ({"price": 100}, None)
    assert "60분 전" in capsys.readouterr().err


def test_an_unauthorized_response_never_falls_back_to_a_stale_body(monkeypatch) -> None:
    """401은 쿠키 문제다 — 지난 값으로 덮으면 쿠키 만료를 못 알아챈다."""
    import os
    import time

    url = "https://example.test/stale"
    cache.store(url, b'{"price": 100}', authed=True)
    old = time.time() - 3600
    os.utime(cache.cache_path(url, authed=True), (old, old))
    _scripted(monkeypatch, [_http_error(401)])

    assert http.get_json(url, authed=True) == (None, "HTTP 401")
