"""market-data `fetch.py` — 단건 시세·일봉 조회."""

from __future__ import annotations

import pytest

from invagent.datafeed import stockeasy as datafeed_stockeasy
from invagent.datafeed.daily import Bars
from tests.conftest import load_script


def test_market_data_fetch_prints_a_quote(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = load_script("market-data", "fetch")
    monkeypatch.setattr(
        module.tickers, "resolve_stock", lambda q: ({"stock_code": "005930"}, None, 0)
    )
    monkeypatch.setattr(datafeed_stockeasy, "load_cookie", lambda: "session=abc")
    monkeypatch.setattr(
        module.stockeasy,
        "fetch_stock_json",
        lambda *a, **k: ({"stock_info": {"cur_prc": "-70000", "flu_rt": "-1.5"}}, None),
    )

    assert module.main(["quote", "삼성전자"]) == 0
    out = capsys.readouterr().out
    assert "005930" in out and "70,000" in out


def test_market_data_fetch_reports_a_failure_without_raising(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """수집 실패는 비블로킹이라는 공통 원칙을 이 CLI도 따른다 — 사유를 stderr에 남긴다."""
    module = load_script("market-data", "fetch")
    monkeypatch.setattr(module.daily, "fetch_daily_bars", lambda code, days, asof=None: Bars([], "HTTP 500"))
    monkeypatch.setattr(
        module.tickers, "resolve_stock", lambda q: ({"stock_code": "005930"}, None, 0)
    )

    assert module.main(["bars", "삼성전자"]) == 1
    assert "HTTP 500" in capsys.readouterr().err


def test_market_data_fetch_bars_names_a_naver_fallback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """직통 조회도 판정용 일봉과 같은 경로다 — 네이버 대체면 시간외가 가능성을 밝힌다."""
    from invagent.datafeed import daily

    module = load_script("market-data", "fetch")
    note = daily.fallback_note("STOCKEASY_COOKIE 미설정")
    monkeypatch.setattr(
        daily,
        "fetch_daily_bars",
        lambda code, days, asof=None: Bars([{"date": "20260928", "close": 121700.0, "volume": 1.0}], fallback=note),
    )
    monkeypatch.setattr(
        module.tickers, "resolve_stock", lambda q: ({"stock_code": "353200"}, None, 0)
    )

    assert module.main(["bars", "대덕전자"]) == 0
    assert note in capsys.readouterr().out


def test_market_data_fetch_passes_an_ambiguous_name_back(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = load_script("market-data", "fetch")
    monkeypatch.setattr(
        module.tickers, "resolve_stock", lambda q: (None, "종목명 후보 다수 — 가나(000001)", 2)
    )

    assert module.main(["quote", "가나"]) == 2
    assert "후보 다수" in capsys.readouterr().err
