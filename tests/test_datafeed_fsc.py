"""`invagent.datafeed.fsc` — 금융위원회 주식시세정보(KRX 공식 정규장 일봉)."""

import json
from datetime import date

import pytest

from invagent.datafeed import cache, fsc, http


def item(
    basDt, srtnCd="353200", mkp="98000", hipr="99600", lopr="96500", clpr="97000", trqu="474752", vs="-3000"
):
    return {
        "basDt": basDt,
        "srtnCd": srtnCd,
        "isinCd": "KR7353200000",
        "itmsNm": "대덕전자",
        "mrktCtg": "KOSPI",
        "clpr": clpr,
        "vs": vs,
        "fltRt": "-3",
        "mkp": mkp,
        "hipr": hipr,
        "lopr": lopr,
        "trqu": trqu,
        "trPrc": "0",
        "lstgStCnt": "0",
        "mrktTotAmt": "0",
    }


def response(*items) -> bytes:
    return json.dumps(
        {
            "response": {
                "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
                "body": {
                    "numOfRows": 1000,
                    "pageNo": 1,
                    "totalCount": len(items),
                    "items": {"item": list(items)},
                },
            }
        }
    ).encode()


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path)


def test_bars_are_the_official_regular_session_prices_oldest_first(monkeypatch) -> None:
    """KRX 공식 종가다 — 대덕전자 9/14는 네이버가 95,600원으로 보인 날의 정규장 종가 97,000원."""
    monkeypatch.setattr(fsc, "load_key", lambda: "k" * 64)
    body = response(
        item("20260914"),
        item("20260911", mkp="99900", hipr="100800", lopr="98100", clpr="100000", trqu="669488"),
    )
    monkeypatch.setattr(http, "read_url", lambda url, headers, timeout: body)

    bars, err = fsc.fetch_bars("353200", 30, asof=date(2026, 9, 14))

    assert err is None
    assert [b["date"] for b in bars] == ["20260911", "20260914"]
    assert bars[-1] == {
        "date": "20260914",
        "open": 98000.0,
        "high": 99600.0,
        "low": 96500.0,
        "close": 97000.0,
        "volume": 474752.0,
    }


def test_bars_before_a_stock_split_are_adjusted_to_the_new_share_count(monkeypatch) -> None:
    """KRX 원 시세는 분할 전 가격 그대로다 — LS ELECTRIC 2026-04-13 5:1 분할에서 788,000 → 179,200원.

    분할일 `vs`는 조정 기준가(157,600원) 대비라 그 비율로 이전 봉을 맞춘다. 거래량은 반대로 곱한다.
    """
    monkeypatch.setattr(fsc, "load_key", lambda: "k" * 64)
    body = response(
        item("20260413", srtnCd="010120", mkp="171200", hipr="182000", lopr="170000", clpr="179200",
             trqu="1000000", vs="21600"),
        item("20260407", srtnCd="010120", mkp="820000", hipr="825000", lopr="780000", clpr="788000",
             trqu="200000", vs="-11000"),
    )
    monkeypatch.setattr(http, "read_url", lambda url, headers, timeout: body)

    bars, err = fsc.fetch_bars("010120", 30, asof=date(2026, 4, 13))

    assert err is None
    assert bars[0] == {
        "date": "20260407",
        "open": 164000.0,
        "high": 165000.0,
        "low": 156000.0,
        "close": 157600.0,
        "volume": 1000000.0,
    }
    assert bars[1]["close"] == 179200.0


def test_a_halted_day_with_no_trade_is_not_a_bar(monkeypatch) -> None:
    """분할 매매정지일(LS ELECTRIC 2026-04-08~10)은 시가·고가·저가 0, 거래량 0으로 온다.

    봉으로 두면 이동평균 창이 StockEasy·네이버보다 정지일 수만큼 밀린다.
    """
    monkeypatch.setattr(fsc, "load_key", lambda: "k" * 64)
    body = response(
        item("20260408", srtnCd="010120", mkp="0", hipr="0", lopr="0", clpr="788000", trqu="0", vs="0"),
        item("20260407", srtnCd="010120", mkp="820000", hipr="825000", lopr="780000", clpr="788000",
             trqu="200000", vs="-11000"),
    )
    monkeypatch.setattr(http, "read_url", lambda url, headers, timeout: body)

    bars, err = fsc.fetch_bars("010120", 30, asof=date(2026, 4, 8))

    assert err is None
    assert [b["date"] for b in bars] == ["20260407"]


def test_codes_that_only_contain_the_ticker_are_dropped(monkeypatch) -> None:
    """`likeSrtnCd`는 부분 일치다 — 다른 종목의 봉이 섞이면 안 된다."""
    monkeypatch.setattr(fsc, "load_key", lambda: "k" * 64)
    body = response(item("20260914"), item("20260914", srtnCd="353201", clpr="1"))
    monkeypatch.setattr(http, "read_url", lambda url, headers, timeout: body)

    bars, err = fsc.fetch_bars("353200", 30, asof=date(2026, 9, 14))

    assert [b["close"] for b in bars] == [97000.0]


def test_a_rejected_key_is_a_failure_named_without_the_key(monkeypatch) -> None:
    """키가 틀려도 HTTP 200으로 오류 본문이 온다 — 빈 일봉이 아니라 실패로, 키 값은 숨긴다."""
    secret = "f" * 64
    monkeypatch.setattr(fsc, "load_key", lambda: secret)
    body = json.dumps(
        {
            "OpenAPI_ServiceResponse": {
                "cmmMsgHeader": {
                    "errMsg": "SERVICE_KEY_IS_NOT_REGISTERED_ERROR",
                    "returnAuthMsg": "등록되지 않은 서비스키",
                    "returnReasonCode": "30",
                }
            }
        }
    ).encode()
    monkeypatch.setattr(http, "read_url", lambda url, headers, timeout: body)

    bars, err = fsc.fetch_bars("353200", 30, asof=date(2026, 9, 14))

    assert bars == []
    assert "SERVICE_KEY_IS_NOT_REGISTERED_ERROR" in err
    assert secret not in err


def test_without_a_key_nothing_is_requested(monkeypatch) -> None:
    def read(url, headers, timeout):
        raise AssertionError("no request without a key")

    monkeypatch.setattr(fsc, "load_key", lambda: None)
    monkeypatch.setattr(http, "read_url", read)

    bars, err = fsc.fetch_bars("353200", 30, asof=date(2026, 9, 14))

    assert bars == [] and "DATA_GO_KR_SERVICE_KEY" in err
