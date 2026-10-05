"""`invagent.datafeed.naver` — 네이버 금융 일봉."""

from datetime import date

import pytest

from invagent.datafeed import cache, http, naver

SAMPLE = (
    "[['날짜','시가','고가','저가','종가','거래량','외국인소진율'],"
    "['20260902',100,110,90,105,1000,50.0],"
    "['20260901',95,99,90,98,900,49.0]]"
)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path)


def test_parse_sise_drops_the_header_and_sorts_ascending() -> None:
    """응답은 표준 JSON이 아니라 작은따옴표 파이썬 리터럴이다."""
    bars = naver.parse_sise(SAMPLE)

    assert [b["date"] for b in bars] == ["20260901", "20260902"]
    assert bars[-1]["close"] == 105.0
    assert bars[-1]["volume"] == 1000.0


def test_parse_sise_skips_malformed_rows_instead_of_failing() -> None:
    bars = naver.parse_sise("[['날짜'],['20260902',1,2,3,4,5],['20260903','x']]")

    assert [b["date"] for b in bars] == ["20260902"]


def test_fetch_bars_asks_the_documented_endpoint_and_window(monkeypatch) -> None:
    seen = {}
    monkeypatch.setattr(
        http, "read_url", lambda url, headers, timeout: seen.update(url=url) or SAMPLE.encode()
    )

    bars, err = naver.fetch_bars("005930", days=30, asof=date(2026, 9, 2))

    assert err is None and len(bars) == 2
    assert seen["url"].startswith(naver.SISE_URL)
    assert "symbol=005930" in seen["url"]
    assert "startTime=20260803" in seen["url"] and "endTime=20260902" in seen["url"]


def test_fetch_bars_reports_the_failure_instead_of_raising(monkeypatch) -> None:
    def boom(url, headers, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr(http, "read_url", boom)

    bars, err = naver.fetch_bars("005930", days=30)

    assert bars == []
    assert err and "timed out" in err


def test_fetch_bars_is_served_from_the_cache_on_a_repeat_call(monkeypatch) -> None:
    """보유 종목마다·지수마다 부르는 함수라 같은 창을 두 번 받지 않는다."""
    calls = []
    monkeypatch.setattr(
        http,
        "read_url",
        lambda url, headers, timeout: calls.append(1) or SAMPLE.encode(),
    )

    naver.fetch_bars("005930", days=30, asof=date(2026, 9, 2))
    naver.fetch_bars("005930", days=30, asof=date(2026, 9, 2))

    assert len(calls) == 1


TREND_SAMPLE = (
    '[{"itemCode":"005930","bizdate":"20261002","foreignerPureBuyQuant":"-350,942",'
    '"organPureBuyQuant":"+614,278","individualPureBuyQuant":"-2,217,946","closePrice":"276,000"},'
    '{"itemCode":"005930","bizdate":"20261001","foreignerPureBuyQuant":"-758,236",'
    '"organPureBuyQuant":"0","individualPureBuyQuant":"+1,292,880","closePrice":"274,500"}]'
)


def test_fetch_investor_trend_parses_signed_quantities_oldest_first(monkeypatch) -> None:
    """기관·외국인 순매수량은 `+614,278` 같은 부호·쉼표 문자열로 온다."""
    seen = {}
    monkeypatch.setattr(
        http, "read_url", lambda url, headers, timeout: seen.update(url=url) or TREND_SAMPLE.encode()
    )

    rows, err = naver.fetch_investor_trend("005930", days=60)

    assert err is None
    assert seen["url"] == "https://m.stock.naver.com/api/stock/005930/trend?pageSize=60"
    assert rows == [
        {"date": "20261001", "institution": 0, "foreign": -758_236, "individual": 1_292_880},
        {"date": "20261002", "institution": 614_278, "foreign": -350_942, "individual": -2_217_946},
    ]


def test_fetch_investor_trend_reports_the_failure_instead_of_raising(monkeypatch) -> None:
    def boom(url, headers, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr(http, "read_url", boom)

    rows, err = naver.fetch_investor_trend("005930")

    assert rows == []
    assert err and "timed out" in err


def test_fetch_listing_market_reads_the_exchange_name(monkeypatch) -> None:
    seen = {}
    body = b'{"itemCode":"213420","stockExchangeType":{"code":"KQ","name":"KOSDAQ"},"stockExchangeName":"KOSDAQ"}'
    monkeypatch.setattr(http, "read_url", lambda url, headers, timeout: seen.update(url=url) or body)

    market, err = naver.fetch_listing_market("213420")

    assert (market, err) == ("KOSDAQ", None)
    assert seen["url"] == "https://m.stock.naver.com/api/stock/213420/basic"


def test_fetch_listing_market_reports_the_failure_instead_of_raising(monkeypatch) -> None:
    def boom(url, headers, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr(http, "read_url", boom)

    market, err = naver.fetch_listing_market("213420")

    assert market is None and "timed out" in err
