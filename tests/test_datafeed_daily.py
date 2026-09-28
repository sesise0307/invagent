"""`invagent.datafeed.daily` — 판정용 일봉 (StockEasy 정규장 우선, 네이버 대체)."""

import json
from datetime import date

import pytest

from invagent.datafeed import cache, daily, http, naver, stockeasy

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


def fake_read(info_tab=INFO_TAB, naver_body=NAVER_SISE, info_error=None):
    def read(url, headers, timeout):
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
