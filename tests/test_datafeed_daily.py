"""`invagent.datafeed.daily` — 판정용 일봉 (금융위 KRX 공식 우선, 최근 봉 StockEasy, 네이버 대체)."""

import json
from datetime import date

import pytest

from invagent.datafeed import cache, daily, fsc, http, naver, stockeasy

INFO_TAB = {
    "stock_code": "353200",
    "chart": {
        "interval": "1d",
        "schema": {
            "fields": ["timestamp", "open", "high", "low", "close", "volume", "price_change_percent"],
        },
        "data": [
            ["2026-09-23T00:00:00+09:00", 113600.0, 120300.0, 111000.0, 119400.0, 1509435, 6.32],
            ["2026-09-28T00:00:00+09:00", 124600.0, 129100.0, 121400.0, 123300.0, 1303836, 3.27],
        ],
    },
}

# 같은 날짜, 장 마감 후 시간외가가 섞인 네이버 값.
NAVER_SISE = (
    "[['날짜','시가','고가','저가','종가','거래량','외국인소진율'],"
    "['20260923',113600,120300,111000,119200,1578412,18.5],"
    "['20260928',124600,129100,121300,121700,1363903,18.0]]"
)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path)


@pytest.fixture(autouse=True)
def _no_official_key(monkeypatch):
    """기본은 금융위 키 없음 — 실제 `.env`에 키가 있어도 테스트는 그 값을 읽지 않는다."""
    monkeypatch.setattr(fsc, "load_key", lambda: None)


def official(*rows) -> str:
    """금융위 응답. 행은 `(basDt, 종가)` — 시가·고가·저가는 종가와 같게 둔다."""
    items = [
        {"basDt": d, "srtnCd": "353200", "mkp": c, "hipr": c, "lopr": c, "clpr": c, "trqu": "1000"}
        for d, c in rows
    ]
    return json.dumps({"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": items}}}})


def fake_read(info_tab=INFO_TAB, naver_body=NAVER_SISE, info_error=None, fsc_body=None):
    def read(url, headers, timeout):
        if url.startswith(fsc.PRICE_URL) and fsc_body is not None:
            return fsc_body.encode()
        if url.startswith(stockeasy.API_BASE):
            if info_error:
                raise info_error
            return json.dumps(info_tab).encode()
        if url.startswith(naver.SISE_URL):
            return naver_body.encode()
        raise AssertionError(f"unexpected url {url}")

    return read


def test_regular_session_close_comes_from_stockeasy_not_naver(monkeypatch) -> None:
    """네이버는 장 마감 후 시간외가를 종가로 보여 준다 — 판정은 StockEasy 정규장 종가로 한다."""
    monkeypatch.setattr(stockeasy, "load_cookie", lambda: "session=abc")
    monkeypatch.setattr(http, "read_url", fake_read())

    bars, err, note = daily.fetch_daily_bars("353200", days=30, asof=date(2026, 9, 28))

    assert err is None and note is None
    assert [b["date"] for b in bars] == ["20260923", "20260928"]
    assert bars[-1] == {
        "date": "20260928",
        "open": 124600.0,
        "high": 129100.0,
        "low": 121400.0,
        "close": 123300.0,
        "volume": 1303836.0,
        "source": "stockeasy",
    }


def test_without_a_cookie_it_falls_back_to_naver_and_says_why(monkeypatch) -> None:
    """쿠키가 없으면 판정은 계속하되, 시간외가가 섞였을 수 있다는 사실을 숨기지 않는다."""
    seen = []

    def read(url, headers, timeout):
        seen.append(url)
        return fake_read()(url, headers, timeout)

    monkeypatch.setattr(stockeasy, "load_cookie", lambda: None)
    monkeypatch.setattr(http, "read_url", read)

    bars, err, note = daily.fetch_daily_bars("353200", days=30, asof=date(2026, 9, 28))

    assert err is None
    assert bars[-1]["close"] == 121700.0
    assert "STOCKEASY_COOKIE" in note and "시간외" in note
    assert not any(url.startswith(stockeasy.API_BASE) for url in seen)


def test_a_failed_stockeasy_call_falls_back_to_naver_with_the_failure_named(monkeypatch) -> None:
    monkeypatch.setattr(stockeasy, "load_cookie", lambda: "session=expired")
    monkeypatch.setattr(http, "read_url", fake_read(info_error=TimeoutError("timed out")))

    bars, err, note = daily.fetch_daily_bars("353200", days=30, asof=date(2026, 9, 28))

    assert err is None
    assert bars[-1]["close"] == 121700.0
    assert "timed out" in note and "시간외" in note


def test_an_info_tab_without_bars_in_the_window_falls_back_to_naver(monkeypatch) -> None:
    """StockEasy chart가 비었거나 창 밖이면 빈 일봉으로 판정을 막지 않는다."""
    monkeypatch.setattr(stockeasy, "load_cookie", lambda: "session=abc")
    monkeypatch.setattr(http, "read_url", fake_read(info_tab={"stock_code": "353200", "chart": None}))

    bars, err, note = daily.fetch_daily_bars("353200", days=30, asof=date(2026, 9, 28))

    assert bars[-1]["close"] == 121700.0
    assert note and "chart" in note


def _kst(*args) -> float:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime(*args, tzinfo=ZoneInfo("Asia/Seoul")).timestamp()


@pytest.mark.parametrize(
    "stored, refetched",
    [
        # 정규장이 끝난 뒤 받은 일봉은 시간외가 움직여도 바뀌지 않는다 — 다음 개장까지 재사용.
        (_kst(2026, 10, 1, 15, 45), False),
        # 장중에 받은 일봉은 마지막 봉이 장중 값이다 — 다시 받는다.
        (_kst(2026, 10, 1, 14, 0), True),
    ],
)
def test_bars_fetched_after_the_regular_close_are_reused_until_the_next_open(
    monkeypatch, stored, refetched
) -> None:
    """StockEasy 요청 한도를 일봉 재조회에 쓰지 않는다 — 정규장 종가는 15:30에 확정된다."""
    import os

    calls = []
    read = fake_read()

    def counting(url, headers, timeout):
        calls.append(url)
        return read(url, headers, timeout)

    monkeypatch.setattr(stockeasy, "load_cookie", lambda: "session=abc")
    monkeypatch.setattr(http, "read_url", counting)
    monkeypatch.delenv(cache.ENV_TTL, raising=False)
    monkeypatch.setattr(cache, "_now", lambda: stored)
    daily.fetch_daily_bars("353200", 30, asof="2026-09-30")
    url = stockeasy.API_BASE + stockeasy.ENDPOINTS["info_tab"].format(code="353200")
    os.utime(cache.cache_path(url, authed=True), (stored, stored))

    monkeypatch.setattr(cache, "_now", lambda: _kst(2026, 10, 1, 18, 0))
    bars, err, note = daily.fetch_daily_bars("353200", 30, asof="2026-09-30")

    assert note is None and bars[-1]["close"] == 123300.0
    assert (len(calls) == 2) is refetched


def test_other_info_tab_callers_keep_the_after_hours_window(monkeypatch) -> None:
    """`fetch_stock_info`는 시간외·NXT 시세를 함께 싣는다 — 20:00 전에는 15분 TTL 그대로."""
    import os

    monkeypatch.setattr(stockeasy, "load_cookie", lambda: "session=abc")
    monkeypatch.setattr(http, "read_url", fake_read())
    monkeypatch.delenv(cache.ENV_TTL, raising=False)
    stored = _kst(2026, 10, 1, 15, 45)
    monkeypatch.setattr(cache, "_now", lambda: stored)
    daily.fetch_daily_bars("353200", 30, asof="2026-09-30")
    url = stockeasy.API_BASE + stockeasy.ENDPOINTS["info_tab"].format(code="353200")
    os.utime(cache.cache_path(url, authed=True), (stored, stored))
    monkeypatch.setattr(cache, "_now", lambda: _kst(2026, 10, 1, 18, 0))

    assert cache.load(url, authed=True) is None


def recording(monkeypatch, **kwargs) -> list[str]:
    seen = []
    read = fake_read(**kwargs)

    def wrapped(url, headers, timeout):
        seen.append(url)
        return read(url, headers, timeout)

    monkeypatch.setattr(http, "read_url", wrapped)
    return seen


@pytest.mark.parametrize(
    "asof",
    [
        "2026-09-25",  # 금요일까지 다 있다
        "2026-09-27",  # 일요일 — 금요일 뒤로 빠진 평일이 없다
    ],
)
def test_official_bars_that_reach_the_last_weekday_need_no_stockeasy_call(monkeypatch, asof) -> None:
    """StockEasy 요청 한도는 공식 일봉에 아직 없는 봉에만 쓴다."""
    monkeypatch.setattr(fsc, "load_key", lambda: "k" * 64)
    monkeypatch.setattr(stockeasy, "load_cookie", lambda: "session=abc")
    seen = recording(monkeypatch, fsc_body=official(("20260924", "118000"), ("20260925", "120000")))

    bars, err, note = daily.fetch_daily_bars("353200", days=30, asof=asof)

    assert err is None and note is None
    assert [(b["date"], b["close"]) for b in bars] == [("20260924", 118000.0), ("20260925", 120000.0)]
    assert not any(url.startswith(stockeasy.API_BASE) for url in seen)


def test_a_bar_not_yet_published_is_taken_from_stockeasy(monkeypatch) -> None:
    """공식 일봉은 다음 영업일 13시 뒤에 올라온다 — 그 사이의 봉만 StockEasy 정규장 봉으로 잇는다."""
    monkeypatch.setattr(fsc, "load_key", lambda: "k" * 64)
    monkeypatch.setattr(stockeasy, "load_cookie", lambda: "session=abc")
    recording(monkeypatch, fsc_body=official(("20260923", "119000"), ("20260925", "120000")))

    bars, err, note = daily.fetch_daily_bars("353200", days=30, asof="2026-09-28")

    assert err is None and note is None
    # 9/23은 공식 값이 그대로, 9/28만 StockEasy에서 붙는다.
    assert [(b["date"], b["close"]) for b in bars] == [
        ("20260923", 119000.0),
        ("20260925", 120000.0),
        ("20260928", 123300.0),
    ]


def test_a_missing_bar_stockeasy_cannot_supply_comes_from_naver_and_is_labelled(monkeypatch) -> None:
    """공식 히스토리는 지키고, 빈 최근 봉만 네이버로 채우되 시간외가가 섞였을 수 있음을 밝힌다."""
    monkeypatch.setattr(fsc, "load_key", lambda: "k" * 64)
    monkeypatch.setattr(stockeasy, "load_cookie", lambda: None)
    recording(monkeypatch, fsc_body=official(("20260923", "119000"), ("20260925", "120000")))

    bars, err, note = daily.fetch_daily_bars("353200", days=30, asof="2026-09-28")

    assert err is None
    assert [(b["date"], b["close"]) for b in bars] == [
        ("20260923", 119000.0),
        ("20260925", 120000.0),
        ("20260928", 121700.0),
    ]
    assert "20260928" in note and "STOCKEASY_COOKIE" in note and "시간외" in note


def test_the_source_label_names_the_official_bars_and_the_stockeasy_tail(monkeypatch) -> None:
    """출처 표기는 실제로 쓴 소스를 밝힌다 — 공식 일봉 구간과 StockEasy가 이은 봉."""
    monkeypatch.setattr(fsc, "load_key", lambda: "k" * 64)
    monkeypatch.setattr(stockeasy, "load_cookie", lambda: "session=abc")
    recording(monkeypatch, fsc_body=official(("20260923", "119000"), ("20260925", "120000")))

    bars, _, note = daily.fetch_daily_bars("353200", days=30, asof="2026-09-28")

    assert daily.source_label(bars, note) == "금융위 KRX 공식 일봉 ~20260925 + StockEasy 정규장 20260928"
