"""`invagent.datafeed.stockinfo` — info-tab 두 축(시세·실적)."""

import json
import urllib.error

import pytest

from invagent.datafeed import http, stockeasy, stockinfo


@pytest.fixture
def cookie(monkeypatch):
    monkeypatch.setattr(stockeasy, "load_cookie", lambda: "session=abc")


def _info_url(code: str) -> str:
    return stockeasy.API_BASE + stockeasy.ENDPOINTS["info_tab"].format(code=code)


def _serve(monkeypatch, payloads: dict[str, dict]) -> list[str]:
    """종목 코드별 info-tab 응답을 돌려주고, 요청된 URL을 기록한다."""
    calls: list[str] = []

    def fake_read(url, headers, timeout):
        calls.append(url)
        for code, payload in payloads.items():
            if url == _info_url(code):
                return json.dumps(payload).encode()
        raise AssertionError(f"예상 밖 요청: {url}")

    monkeypatch.setattr(http, "read_url", fake_read)
    return calls


def test_nothing_is_requested_until_an_axis_is_read(monkeypatch, cookie) -> None:
    calls = _serve(monkeypatch, {"005930": {"stock_info": {"name": "삼성전자"}}})

    info = stockinfo.fetch("005930")

    assert calls == []
    assert info.is_preferred is False


def test_a_common_share_reads_both_axes_from_one_request(monkeypatch, cookie) -> None:
    payload = {"stock_info": {"name": "삼성전자"}}
    calls = _serve(monkeypatch, {"005930": payload})

    info = stockinfo.fetch("005930")

    assert info.fundamentals == payload
    assert info.price == payload
    assert calls == [_info_url("005930")]
    assert info.parent_code is None
    assert info.sources == ["StockEasy info-tab"]
    assert info.gaps == []


def test_a_preferred_shares_fundamentals_cost_one_request_on_the_parent(monkeypatch, cookie) -> None:
    calls = _serve(monkeypatch, {"005930": {"stock_info": {"name": "삼성전자"}}})

    info = stockinfo.fetch("005935")

    assert info.fundamentals == {"stock_info": {"name": "삼성전자"}}
    assert calls == [_info_url("005930")], "우선주 자기 info-tab은 받지 않는다"
    assert (info.is_preferred, info.parent_code, info.parent_name) == (True, "005930", "삼성전자")
    assert info.sources == ["우선주 실적은 본주 삼성전자(005930)", "StockEasy info-tab"]


def test_a_preferred_share_reading_both_axes_costs_two_requests(monkeypatch, cookie) -> None:
    calls = _serve(
        monkeypatch,
        {
            "005930": {"stock_info": {"name": "삼성전자", "cur_prc": 80000}},
            "005935": {"stock_info": {"name": "삼성전자우", "cur_prc": 61000}},
        },
    )

    info = stockinfo.fetch("005935")

    assert info.price["stock_info"]["cur_prc"] == 61000, "시세 축은 우선주 자기 것"
    assert info.fundamentals["stock_info"]["cur_prc"] == 80000
    assert info.price is info.price
    assert sorted(calls) == [_info_url("005930"), _info_url("005935")]


def test_the_source_line_falls_back_to_the_parent_code_without_a_name(monkeypatch, cookie) -> None:
    _serve(monkeypatch, {"005930": {"financials": {}}})

    info = stockinfo.fetch("005935")
    info.fundamentals

    assert info.sources[0] == "우선주 실적은 본주 005930"


def _refuse_401(monkeypatch) -> None:
    def fake_read(url, headers, timeout):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(http, "read_url", fake_read)


def test_a_401_with_a_cookie_names_an_expired_cookie(monkeypatch, cookie) -> None:
    _refuse_401(monkeypatch)

    info = stockinfo.fetch("005930")

    assert info.fundamentals is None
    assert info.gaps == ["HTTP 401 (쿠키 만료·무효)"]
    assert info.sources == []


def test_a_401_without_a_cookie_names_the_missing_variable(monkeypatch) -> None:
    monkeypatch.setattr(stockeasy, "load_cookie", lambda: None)
    _refuse_401(monkeypatch)

    info = stockinfo.fetch("005930")
    info.fundamentals

    assert info.gaps == ["HTTP 401 (STOCKEASY_COOKIE 미설정)"]


def test_an_empty_response_is_a_gap(monkeypatch, cookie) -> None:
    monkeypatch.setattr(http, "read_url", lambda url, headers, timeout: b"{}")

    info = stockinfo.fetch("005930")

    assert info.fundamentals is None
    assert info.gaps == ["빈 응답"]


def test_a_failed_axis_is_not_requested_twice(monkeypatch, cookie) -> None:
    calls = []

    def fake_read(url, headers, timeout):
        calls.append(url)
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(http, "read_url", fake_read)

    info = stockinfo.fetch("005930")
    info.fundamentals
    info.price

    assert len(calls) == 1
    assert len(info.gaps) == 1


def _financials_payload(primary: str) -> dict:
    return {
        "primary_fs_type": primary,
        "financials": {
            "consolidated": [
                {"year": 2026, "quarter": 1, "operating_income": 100},
                {"year": 2026, "quarter": 2, "operating_income": None},
                {"year": 2025, "quarter": 4},
            ],
            "consolidatedEstimate": [{"year": 2026, "quarter": 3, "operating_income": 130}],
            "consolidatedYearly": [{"year": 2025, "operating_income": 400}],
            "consolidatedYearlyEstimate": [{"year": 2026, "operating_income": 500}],
            "separate": [{"year": 2026, "quarter": 1, "operating_income": 7}],
            "separateEstimate": [{"year": 2026, "quarter": 2, "operating_income": 8}],
            "separateYearly": [{"year": 2025, "operating_income": 30}],
            "separateYearlyEstimate": [],
        },
    }


def test_quarterly_reads_confirmed_rows_on_the_consolidated_statement(monkeypatch, cookie) -> None:
    _serve(monkeypatch, {"005930": _financials_payload("C")})

    info = stockinfo.fetch("005930")

    assert info.quarterly("operating_income") == {(2026, 1): 100}, "값 없는 행은 건너뛴다"
    assert info.quarterly("operating_income", estimates=True) == {(2026, 3): 130}
    assert info.yearly("operating_income") == {(2025, None): 400}
    assert info.yearly("operating_income", estimates=True) == {(2026, None): 500}


def test_a_separate_primary_statement_reads_the_separate_rows(monkeypatch, cookie) -> None:
    _serve(monkeypatch, {"005930": _financials_payload("S")})

    info = stockinfo.fetch("005930")

    assert info.quarterly("operating_income") == {(2026, 1): 7}
    assert info.quarterly("operating_income", estimates=True) == {(2026, 2): 8}
    assert info.yearly("operating_income") == {(2025, None): 30}
    assert info.yearly("operating_income", estimates=True) == {}


def test_series_are_empty_when_the_fundamentals_axis_failed(monkeypatch, cookie) -> None:
    _refuse_401(monkeypatch)

    assert stockinfo.fetch("005930").quarterly("operating_income") == {}


def test_a_preferred_shares_series_come_from_the_parent(monkeypatch, cookie) -> None:
    calls = _serve(monkeypatch, {"005930": _financials_payload("C")})

    assert stockinfo.fetch("005935").quarterly("operating_income") == {(2026, 1): 100}
    assert calls == [_info_url("005930")]


def test_rows_return_whole_rows_of_the_primary_statement(monkeypatch, cookie) -> None:
    """한 기간의 여러 값(매출·영업이익·EPS…)을 함께 찍는 표는 시계열 하나가 아니라 행이 필요하다."""
    _serve(monkeypatch, {"005930": _financials_payload("S")})

    info = stockinfo.fetch("005930")

    assert info.primary_fs_type == "S"
    assert info.rows() == [{"year": 2026, "quarter": 1, "operating_income": 7}]
    assert info.rows(estimates=True) == [{"year": 2026, "quarter": 2, "operating_income": 8}]
    assert info.rows(yearly=True) == [{"year": 2025, "operating_income": 30}]
    assert info.rows(yearly=True, estimates=True) == []


def test_rows_are_empty_and_the_statement_defaults_to_consolidated_without_fundamentals(
    monkeypatch, cookie
) -> None:
    _refuse_401(monkeypatch)

    info = stockinfo.fetch("005930")

    assert info.rows() == []
    assert info.primary_fs_type == "C"


def test_name_is_the_shares_own_name_once_its_payload_is_in(monkeypatch, cookie) -> None:
    calls = _serve(
        monkeypatch,
        {
            "005930": {"stock_info": {"name": "삼성전자"}},
            "005935": {"stock_info": {"name": "삼성전자우"}},
        },
    )

    common = stockinfo.fetch("005930")
    common.fundamentals
    preferred = stockinfo.fetch("005935")
    preferred.fundamentals

    assert common.name == "삼성전자"
    assert preferred.name is None, "본주 이름을 우선주 이름으로 내지 않는다 — 자기 시세 축을 받기 전엔 모른다"
    assert len(calls) == 1, "본주 info-tab은 캐시에서 다시 쓴다"
    preferred.price
    assert preferred.name == "삼성전자우"
