"""`invagent.datafeed.stockeasy` — StockEasy 종목·시장 API."""

from invagent.datafeed import cache, http, stockeasy


def test_endpoints_sit_on_the_documented_api_hosts() -> None:
    assert stockeasy.API_BASE == "https://stockeasy.intellio.kr/stockdata/api/v1"
    assert set(stockeasy.ENDPOINTS) == {"search", "info_tab", "analysis_tab", "news", "reports"}
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


def test_an_alphanumeric_krx_code_is_a_ticker_and_costs_no_call(monkeypatch) -> None:
    """신형 코드(ESS ETF `0209D0`)도 이름 검색으로 새지 않는다."""
    def boom(*a, **k):
        raise AssertionError("티커는 검색 없이 그대로 쓴다")

    monkeypatch.setattr(http, "read_url", boom)

    hit, err, code = stockeasy.resolve_stock("0209D0")

    assert (hit["stock_code"], err, code) == ("0209D0", None, 0)


def test_a_name_starting_with_a_letter_is_never_a_ticker(monkeypatch) -> None:
    """영문 종목명이 여섯 글자여도 첫 자리가 숫자가 아니면 검색한다."""
    monkeypatch.setattr(
        stockeasy,
        "fetch_stock_json",
        lambda *a, **k: ([{"stock_code": "035420", "stock_name": "NAVERX", "market": "KR"}], None),
    )

    hit, err, code = stockeasy.resolve_stock("NAVERX")

    assert (hit["stock_code"], err, code) == ("035420", None, 0)


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


def test_gap_reason_names_the_cookie_only_when_it_is_the_likely_cause() -> None:
    assert stockeasy.gap_reason("HTTP 401", "session=abc") == "HTTP 401 (쿠키 만료·무효)"
    assert stockeasy.gap_reason("HTTP 401", None) == "HTTP 401 (STOCKEASY_COOKIE 미설정)"
    assert stockeasy.gap_reason("HTTP 503", "session=abc") == "HTTP 503"
    assert stockeasy.gap_reason(None, "session=abc") == "빈 응답"
