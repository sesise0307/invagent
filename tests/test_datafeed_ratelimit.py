"""`invagent.datafeed.http` + `ratelimit` — 호스트 요청 제한을 피하는 재시도·간격·차단."""

import urllib.error

from invagent.datafeed import cache, http, ratelimit


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


STOCKEASY = "https://stockeasy.intellio.kr/stockdata/api/v1"


def test_stockeasy_requests_are_spaced_wider_than_other_hosts(monkeypatch) -> None:
    """순차 실행도 끊겼다 — 10/05 3초 미만 간격 14종목째, 10/06 3초 간격 12건째. StockEasy만 8초로 넓힌다."""
    slept = _record_sleeps(monkeypatch)
    _scripted(monkeypatch, [b"{}"] * 4)

    http.get_json(f"{STOCKEASY}/a", authed=False)
    http.get_json(f"{STOCKEASY}/b", authed=False)
    http.get_json("https://example.test/a", authed=False)
    http.get_json("https://example.test/b", authed=False)

    assert slept == [8.0, ratelimit.MIN_INTERVAL_SECONDS]


def test_stockeasy_cools_down_for_an_hour(monkeypatch) -> None:
    """10분 cool-down도 짧았다 — 2026-10-16 15:08 KST에 끊긴 뒤 15:44 요청도 끊겼다. 1시간 쉰다."""
    clock = _Clock(1_000_000.0)
    monkeypatch.setattr(ratelimit, "_now", clock)
    _scripted(monkeypatch, [_http_error(429)] * ratelimit.MAX_ATTEMPTS + [b'{"ok": 1}'])

    http.get_json(f"{STOCKEASY}/a", authed=False)

    clock.now += ratelimit.COOLDOWN_SECONDS + 1
    assert http.get_json(f"{STOCKEASY}/a", authed=False)[0] is None
    clock.now += 3000
    assert http.get_json(f"{STOCKEASY}/a", authed=False)[0] is None
    clock.now += 600
    assert http.get_json(f"{STOCKEASY}/a", authed=False) == ({"ok": 1}, None)


def test_stockeasy_takes_one_request_at_a_time(monkeypatch) -> None:
    import threading
    import time

    lock = threading.Lock()
    active, peak = [0], [0]

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
        threading.Thread(target=http.get_json, args=(f"{STOCKEASY}/{i}",), kwargs={"authed": False})
        for i in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert peak[0] == 1


def _cool_down_with(monkeypatch, error):
    """error로 재시도를 다 쓰게 해 호스트를 cool-down에 넣고, 다음 호출의 오류 메시지를 돌려준다."""
    monkeypatch.setattr(ratelimit, "_now", _Clock(1_000_000.0))
    _scripted(monkeypatch, [error] * ratelimit.MAX_ATTEMPTS)
    http.get_json("https://example.test/a", authed=False)
    payload, err = http.get_json("https://example.test/b", authed=False)
    assert payload is None
    return err


def test_a_cool_down_names_the_http_status_that_caused_it(monkeypatch) -> None:
    """차단인지 네트워크 순단인지는 쿨다운 메시지만 보고 알 수 있어야 한다."""
    err = _cool_down_with(monkeypatch, _http_error(429))

    assert "사유 HTTP 429" in err


def test_a_cool_down_after_dropped_connections_says_so(monkeypatch) -> None:
    err = _cool_down_with(monkeypatch, ConnectionResetError("Connection reset by peer"))

    assert "사유 연결 끊김: Connection reset by peer" in err


def test_a_cool_down_after_an_unreachable_host_says_the_connection_dropped(monkeypatch) -> None:
    err = _cool_down_with(monkeypatch, urllib.error.URLError("Remote end closed connection without response"))

    assert "사유 연결 끊김: Remote end closed connection without response" in err


def test_a_cool_down_after_timeouts_says_they_timed_out(monkeypatch) -> None:
    err = _cool_down_with(monkeypatch, TimeoutError("timed out"))

    assert "사유 시간 초과" in err


def test_a_timeout_wrapped_in_a_url_error_is_still_a_timeout(monkeypatch) -> None:
    """urllib은 소켓 타임아웃을 URLError로 감싸 던지기도 한다."""
    err = _cool_down_with(monkeypatch, urllib.error.URLError(TimeoutError("timed out")))

    assert "사유 시간 초과" in err


def test_a_cool_down_file_from_before_reasons_were_recorded_still_holds(monkeypatch) -> None:
    """배포 전에 쓰인 파일(해제 시각 숫자 하나)도 cool-down으로 지켜야 한다."""
    monkeypatch.setattr(ratelimit, "_now", _Clock(1_000_000.0))
    ratelimit.STATE_ROOT.mkdir(parents=True, exist_ok=True)
    (ratelimit.STATE_ROOT / "example.test.cooldown").write_text("1000300.0")
    calls = _scripted(monkeypatch, [b'{"ok": 1}'])

    payload, err = http.get_json("https://example.test/a", authed=False)

    assert payload is None and calls == []
    assert "300초 남음 · 사유 미기록" in err


def test_a_name_resolution_failure_does_not_cool_the_host_down(monkeypatch) -> None:
    """DNS 실패는 서버 한도와 무관하다 — 네트워크가 막힌 샌드박스 하나가 모든 프로세스를 막으면 안 된다.

    2026-10-14 Codex 샌드박스(네트워크 차단)에서 돈 스크립트가 `[Errno 8]`로 StockEasy를 600초
    cool-down에 넣어, 네트워크가 멀쩡한 다른 세션까지 막혔다.
    """
    import socket

    dns_failure = urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided, or not known"))
    calls = _scripted(monkeypatch, [dns_failure] * ratelimit.MAX_ATTEMPTS + [b'{"ok": 1}'])

    first = http.get_json("https://example.test/a", authed=False)

    assert first[0] is None and "nodename nor servname" in first[1]
    assert http.get_json("https://example.test/b", authed=False) == ({"ok": 1}, None)
    assert len(calls) == ratelimit.MAX_ATTEMPTS + 1


def test_a_cooled_down_login_endpoint_leaves_the_anonymous_market_endpoint_open(monkeypatch) -> None:
    """2026-10-16 StockEasy는 로그인 `info-tab`만 끊고 무인증 시장 지표는 줬다 — 계정 제한이다."""
    calls = _scripted(monkeypatch, [ConnectionResetError("reset")] * ratelimit.MAX_ATTEMPTS + [b'{"ok": 1}'])

    assert http.get_json(f"{STOCKEASY}/info-tab", authed=True)[0] is None
    assert http.get_json(f"{STOCKEASY}/market", authed=False) == ({"ok": 1}, None)
    assert http.get_json(f"{STOCKEASY}/info-tab-2", authed=True)[0] is None
    assert len(calls) == ratelimit.MAX_ATTEMPTS + 1


def _small_budget(monkeypatch, count, window):
    limits = {**ratelimit.HOST_LIMITS["stockeasy.intellio.kr"], "login_budget": count, "login_window": window}
    monkeypatch.setitem(ratelimit.HOST_LIMITS, "stockeasy.intellio.kr", limits)


def test_login_requests_stop_at_the_budget_until_the_oldest_leaves_the_window(monkeypatch) -> None:
    """한도는 KST 날짜로 풀리지 않았다 — 2026-10-16 같은 날 오후에 브라우저가 다시 열렸다. 최근 구간으로 센다."""
    clock = _Clock(1_000_000.0)
    monkeypatch.setattr(ratelimit, "_now", clock)
    _small_budget(monkeypatch, 2, 3600)
    calls = _scripted(monkeypatch, [b'{"ok": 1}'] * 5)

    assert http.get_json(f"{STOCKEASY}/a", authed=True)[0] == {"ok": 1}
    clock.now += 600
    assert http.get_json(f"{STOCKEASY}/b", authed=True)[0] == {"ok": 1}
    payload, err = http.get_json(f"{STOCKEASY}/c", authed=True)
    assert payload is None and "요청 예산 소진" in err and "2/2" in err
    assert http.get_json(f"{STOCKEASY}/market", authed=False)[0] == {"ok": 1}
    assert len(calls) == 3

    clock.now += 3001  # 첫 요청이 1시간 구간을 벗어났다
    assert http.get_json(f"{STOCKEASY}/c", authed=True)[0] == {"ok": 1}


def test_stockeasy_allows_eighty_login_requests_per_six_hours(monkeypatch) -> None:
    """60건에서 2026-10-19 70건, 2026-10-21 80건으로 올렸다(사용자 결정) — 종목 분석이 예산 끝에서 끊겼다."""
    clock = _Clock(1_000_000.0)
    monkeypatch.setattr(ratelimit, "_now", clock)
    calls = _scripted(monkeypatch, [b'{"ok": 1}'] * 81)

    for i in range(80):
        clock.now += 60
        assert http.get_json(f"{STOCKEASY}/r{i}", authed=True)[0] == {"ok": 1}
    payload, err = http.get_json(f"{STOCKEASY}/r80", authed=True)
    assert payload is None and "최근 6시간 80/80건" in err
    assert len(calls) == 80


def test_every_stockeasy_request_is_logged_with_its_outcome(monkeypatch) -> None:
    """다음 차단 때 직전 요청 이력을 보고 차단 방식(일정 시간 차단 · 최근 구간 누적)을 가린다."""
    clock = _Clock(1_000_000.0)
    monkeypatch.setattr(ratelimit, "_now", clock)
    _scripted(monkeypatch, [b'{"ok": 1}', b'{"ok": 1}'] + [ConnectionResetError("reset")] * ratelimit.MAX_ATTEMPTS)

    http.get_json(f"{STOCKEASY}/market", authed=False)
    http.get_json(f"{STOCKEASY}/a", authed=True)
    http.get_json(f"{STOCKEASY}/b", authed=True)

    log = ratelimit.request_log("stockeasy.intellio.kr")
    assert [(e["authed"], e["ok"]) for e in log] == [(False, True), (True, True)] + [(True, False)] * 3
    assert log[-1]["reason"].startswith("연결 끊김")
    assert log[0]["t"] == 1_000_000.0
