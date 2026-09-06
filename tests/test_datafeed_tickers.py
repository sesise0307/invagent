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
