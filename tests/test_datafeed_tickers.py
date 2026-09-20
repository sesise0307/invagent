"""`invagent.datafeed.tickers` — 종목명 → 6자리 티커."""

from invagent.datafeed import stockeasy, tickers

OVERRIDE_FILE = """# 티커 오버라이드

> 설명 줄은 무시된다.

리브스메디 = 491000   # 시트 표기 오타
망가진줄 = 12
"""


def test_load_overrides_reads_only_well_formed_pairs(tmp_path) -> None:
    path = tmp_path / "ticker_overrides.md"
    path.write_text(OVERRIDE_FILE, encoding="utf-8")

    assert tickers.load_overrides(path) == {"리브스메디": "491000"}


def test_load_overrides_is_empty_when_the_file_is_missing(tmp_path) -> None:
    assert tickers.load_overrides(tmp_path / "nope.md") == {}


def test_an_override_wins_over_the_search_api(monkeypatch) -> None:
    def boom(name):
        raise AssertionError("오버라이드가 있으면 검색하지 않는다")

    monkeypatch.setattr(stockeasy, "resolve_stock", boom)

    assert tickers.resolve_code("리브스메디", {"리브스메디": "491000"}) == ("491000", None)


def test_resolve_code_falls_back_to_the_search_api(monkeypatch) -> None:
    monkeypatch.setattr(
        stockeasy, "resolve_stock", lambda name: ({"stock_code": "000660"}, None, 0)
    )

    assert tickers.resolve_code("SK하이닉스", {}) == ("000660", None)


def test_resolve_code_reports_the_reason_when_the_api_cannot_answer(monkeypatch) -> None:
    monkeypatch.setattr(stockeasy, "resolve_stock", lambda name: (None, "검색 결과 없음", 1))

    code, err = tickers.resolve_code("없는종목", {})

    assert code is None and err == "검색 결과 없음"


def test_resolve_stock_uses_an_override_before_the_search_api(monkeypatch) -> None:
    """오버라이드 파일이 스스로 'API보다 먼저 본다'고 규정한다 — 경로는 하나여야 한다."""
    def boom(name):
        raise AssertionError("오버라이드가 있으면 검색하지 않는다")

    monkeypatch.setattr(stockeasy, "resolve_stock", boom)

    hit, err, code = tickers.resolve_stock("리브스메디", {"리브스메디": "491000"})

    assert (hit["stock_code"], hit["stock_name"], err, code) == ("491000", "리브스메디", None, 0)


def test_resolve_stock_delegates_when_no_override_matches(monkeypatch) -> None:
    monkeypatch.setattr(
        stockeasy, "resolve_stock", lambda name: (None, "종목명 후보 다수 — …", 2)
    )

    hit, err, code = tickers.resolve_stock("가나", {})

    assert hit is None and code == 2


def test_common_code_maps_a_preferred_share_to_its_common_share() -> None:
    assert tickers.common_code("005935") == "005930"   # 삼성전자우
    assert tickers.common_code("005387") == "005380"   # 현대차2우B
    assert tickers.common_code("02826K") == "028260"   # 삼성물산우B (신형)
    assert tickers.common_code("03473K") == "034730"   # SK우 (신형)


def test_common_code_is_none_for_a_common_share() -> None:
    assert tickers.common_code("005930") is None
    assert tickers.common_code("006800") is None   # 미래에셋증권 — 이름이 '우'로 끝나던 본주
    assert tickers.common_code("0009K0") is None   # 에임드바이오 — 신형 보통주 코드
    assert tickers.common_code("353200") is None


def test_common_code_ignores_a_malformed_code() -> None:
    assert tickers.common_code("12345") is None
    assert tickers.common_code("") is None


def test_fundamentals_code_swaps_a_preferred_share_for_its_common_share(monkeypatch) -> None:
    def boom(name):
        raise AssertionError("코드 축은 네트워크를 타지 않는다")

    monkeypatch.setattr(stockeasy, "resolve_stock", boom)

    assert tickers.fundamentals_code("005935") == ("005930", True)
    assert tickers.fundamentals_code("005930") == ("005930", False)
    assert tickers.fundamentals_code("006800") == ("006800", False)
