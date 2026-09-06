"""`invagent.datafeed.stockeasy` — StockEasy 종목·시장 API."""

import pytest

from invagent.datafeed import cache, http, stockeasy


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path)


def test_endpoints_sit_on_the_documented_api_hosts() -> None:
    assert stockeasy.API_BASE == "https://stockeasy.intellio.kr/stockdata/api/v1"
    assert set(stockeasy.ENDPOINTS) == {"search", "info_tab", "news", "reports"}
    assert stockeasy.MARKET_API_BASE == "https://stockeasy.intellio.kr/stockdata/api/v1/market"
    assert set(stockeasy.MARKET_ENDPOINTS) == {
        "indices",
        "big_picture",
        "market_monitor",
        "credit_balance",
    }


def test_the_cookie_is_sent_and_marks_the_call_authenticated(monkeypatch) -> None:
    seen = {}

    def fake_read(url, headers, timeout):
        seen["url"] = url
        seen["cookie"] = headers.get("Cookie")
        return b"{}"

    monkeypatch.setattr(http, "read_url", fake_read)
    stockeasy.fetch_stock_json("/stock-info/info-tab/005930", cookie="session=abc")

    assert seen["cookie"] == "session=abc"
    assert seen["url"] == stockeasy.API_BASE + "/stock-info/info-tab/005930"
    assert cache.load(seen["url"], authed=True) == b"{}"
    assert cache.load(seen["url"], authed=False) is None


def test_market_calls_are_always_anonymous(monkeypatch) -> None:
    seen = {}
    monkeypatch.setattr(
        http, "read_url", lambda url, headers, timeout: seen.update(url=url, h=headers) or b"{}"
    )

    stockeasy.fetch_market_json("indices")

    assert seen["url"] == stockeasy.MARKET_API_BASE + "/indices"
    assert "Cookie" not in seen["h"]


def test_a_six_digit_query_is_a_ticker_and_costs_no_call(monkeypatch) -> None:
    def boom(*a, **k):
        raise AssertionError("티커는 검색 없이 그대로 쓴다")

    monkeypatch.setattr(http, "read_url", boom)

    hit, err, code = stockeasy.resolve_stock("005930")

    assert (hit["stock_code"], err, code) == ("005930", None, 0)


def test_an_ambiguous_name_exits_two_and_names_the_candidates(monkeypatch) -> None:
    monkeypatch.setattr(
        stockeasy,
        "fetch_stock_json",
        lambda *a, **k: (
            [
                {"stock_code": "000001", "stock_name": "가나전자", "market": "KR", "exchange": "KOSPI"},
                {"stock_code": "000002", "stock_name": "가나전기", "market": "KR", "exchange": "KOSDAQ"},
            ],
            None,
        ),
    )

    hit, err, code = stockeasy.resolve_stock("가나")

    assert hit is None
    assert code == 2
    assert "가나전자(000001, KOSPI)" in err


def test_fs_rows_follows_the_primary_statement_type() -> None:
    financials = {
        "consolidated": [{"year": 2026}],
        "consolidatedEstimate": [{"year": 2027}],
        "separate": [{"year": 2025}],
        "separateEstimate": [],
    }

    assert stockeasy.fs_rows(financials, "C", yearly=False) == ([{"year": 2026}], [{"year": 2027}])
    assert stockeasy.fs_rows(financials, "S", yearly=False) == ([{"year": 2025}], [])
