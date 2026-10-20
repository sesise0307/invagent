"""`quick-brief/scripts/brief_prep.py` — 보유·관심 종목 시세·뉴스 수집 (StockEasy 미사용)."""

import json

import pytest

from invagent.datafeed import fsc, http, naver
from tests.conftest import load_script

brief_prep = load_script("quick-brief", "brief_prep")

SNAPSHOT = """# 포트폴리오 스냅샷 — 2026-10-19

## 보유 (1종목 + 현금)

| 계좌 | 종목 | 섹터 | 보유 | 평단 | 현재가 | 매수금액 | 수익률 | 비중 | 평가금액 | 투자 아이디어 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 은행 | _현금 | 현금 | 1 | ₩100 | ₩100 | ₩100 | 0.00% | 50.0% | ₩100 |  |
| 삼성 | 대덕전자 | AI | 150 | ₩100,000 | ₩137,500 | ₩15,000,000 | 37.50% | 4.2% | ₩20,625,000 |  |

## 룰 자동 판정

- 매매규칙 6(-20% 전량 매도): 해당 없음
- 매매규칙 11(24~30%↑ 익절 쿠션): 대덕전자 +37.50% (비중 4.2%)

## 다음 절

- 이 줄은 룰 판정이 아니다
"""

WATCHLIST = """# 관심 종목

- 대덕전자
- 삼양식품
"""

CODES = {"대덕전자": "353200", "삼양식품": "003230"}


def _search(query: str) -> bytes:
    code = CODES[query]
    return json.dumps(
        {"items": [{"code": code, "name": query, "typeCode": "KOSPI", "nationCode": "KOR", "category": "stock"}]}
    ).encode()


def _basic(code: str, price: str, pct: str) -> bytes:
    return json.dumps(
        {"itemCode": code, "stockName": code, "closePrice": price, "compareToPreviousClosePrice": "0",
         "fluctuationsRatio": pct, "marketStatus": "OPEN", "localTradedAt": "2026-10-20T10:00:00+09:00"}
    ).encode()


def _news(*rows: tuple[str, str]) -> bytes:
    return json.dumps(
        [{"items": [{"datetime": day, "officeName": "언론", "title": title, "mobileNewsUrl": "u"}]}
         for day, title in rows]
    ).encode()


def _fsc(code: str, close: str) -> bytes:
    item = {"basDt": "20261019", "srtnCd": code, "mkp": close, "hipr": close, "lopr": close,
            "clpr": close, "trqu": "100", "vs": "0"}
    return json.dumps({"response": {"body": {"items": {"item": [item]}}}}).encode()


@pytest.fixture
def files(tmp_path):
    snapshot = tmp_path / "snap.md"
    snapshot.write_text(SNAPSHOT, encoding="utf-8")
    watchlist = tmp_path / "watch.md"
    watchlist.write_text(WATCHLIST, encoding="utf-8")
    return ["--snapshot", str(snapshot), "--watchlist", str(watchlist), "--asof", "2026-10-20"]


def _route(responses: dict):
    """URL 앞부분 → 응답. StockEasy 등 목록에 없는 호스트는 테스트를 깨뜨린다."""
    def read(url, headers, timeout):
        if url.startswith(naver.SEARCH_URL):
            query = url.split("q=")[1].split("&")[0]
            import urllib.parse
            return _search(urllib.parse.unquote_plus(query))
        for prefix, body in responses.items():
            if url.startswith(prefix):
                if isinstance(body, Exception):
                    raise body
                return body
        raise AssertionError(f"허용되지 않은 요청: {url}")
    return read


def test_holdings_come_first_with_return_against_the_average_cost(monkeypatch, files, capsys) -> None:
    monkeypatch.setattr(fsc, "load_key", lambda: "k")
    monkeypatch.setattr(http, "read_url", _route({
        naver.BASIC_URL.format(code="353200"): _basic("353200", "140,000", "+1.82"),
        naver.BASIC_URL.format(code="003230"): _basic("003230", "1,200,000", "-0.50"),
        naver.NEWS_URL.format(code="353200"): _news(("202610200800", "대덕 수주"), ("202610150800", "오래된 기사")),
        naver.NEWS_URL.format(code="003230"): _news(),
        fsc.PRICE_URL + "?serviceKey=k&resultType=json&likeSrtnCd=353200": _fsc("353200", "137500"),
        fsc.PRICE_URL + "?serviceKey=k&resultType=json&likeSrtnCd=003230": _fsc("003230", "1206000"),
    }))

    assert brief_prep.main([*files, "--days", "2", "--json"]) == 0

    out = json.loads(capsys.readouterr().out)
    first, second = out["stocks"]
    assert (first["name"], first["code"], first["held"]) == ("대덕전자", "353200", True)
    assert first["price"] == 140000.0 and first["change_pct"] == 1.82
    assert first["return_pct"] == pytest.approx(40.0)
    assert first["weight"] == "4.2%"
    assert first["official_close"] == {"date": "20261019", "close": 137500.0}
    assert [n["title"] for n in first["news"]] == ["대덕 수주"]
    assert (second["name"], second["held"], second["return_pct"]) == ("삼양식품", False, None)
    assert out["snapshot_date"] == "2026-10-19"


def test_a_failed_source_leaves_a_reason_and_the_rest_still_reports(monkeypatch, files, capsys) -> None:
    monkeypatch.setattr(fsc, "load_key", lambda: None)
    monkeypatch.setattr(http, "read_url", _route({
        # 타임아웃은 호스트 쿨다운을 걸어 뒤 요청까지 막는다 — 종목 하나만 깨지는 실패로 본다.
        naver.BASIC_URL.format(code="353200"): b'{"stockName":"X"}',
        naver.BASIC_URL.format(code="003230"): _basic("003230", "1,200,000", "-0.50"),
        naver.NEWS_URL.format(code="353200"): _news(),
        naver.NEWS_URL.format(code="003230"): _news(),
    }))

    assert brief_prep.main(files) == 0

    out = capsys.readouterr().out
    assert "| 보유 | 대덕전자 | 미수집 |" in out
    assert "| 관심 | 삼양식품 | 1,200,000 | -0.50% |" in out
    assert "대덕전자: 현재가: 현재가 필드 없음" in out


def test_exits_one_when_no_price_was_collected(monkeypatch, files, capsys) -> None:
    monkeypatch.setattr(fsc, "load_key", lambda: None)
    monkeypatch.setattr(http, "read_url", _route({
        naver.BASIC_URL.format(code="353200"): TimeoutError("timed out"),
        naver.BASIC_URL.format(code="003230"): TimeoutError("timed out"),
        naver.NEWS_URL.format(code="353200"): _news(),
        naver.NEWS_URL.format(code="003230"): _news(),
    }))

    assert brief_prep.main(files) == 1


def test_the_default_asof_is_the_kst_date_not_the_machine_date(monkeypatch, files, capsys) -> None:
    """2026-10-20 19:32 CEST 실행이 10/20으로 잡혔다 — KST로는 이미 10/21 02:32였다."""
    from datetime import datetime, timezone

    from invagent.datafeed import cache

    monkeypatch.setattr(cache, "_now", lambda: datetime(2026, 10, 20, 17, 32, tzinfo=timezone.utc).timestamp())
    monkeypatch.setattr(fsc, "load_key", lambda: None)
    monkeypatch.setattr(http, "read_url", _route({
        naver.BASIC_URL.format(code="353200"): _basic("353200", "140,000", "+1.82"),
        naver.BASIC_URL.format(code="003230"): _basic("003230", "1,200,000", "-0.50"),
        naver.NEWS_URL.format(code="353200"): _news(),
        naver.NEWS_URL.format(code="003230"): _news(),
    }))
    no_asof = [arg for arg in files if arg not in ("--asof", "2026-10-20")]

    assert brief_prep.main([*no_asof, "--days", "2", "--json"]) == 0

    out = json.loads(capsys.readouterr().out)
    assert (out["asof"], out["since"]) == ("2026-10-21", "20261019")


def _quiet_routes() -> dict:
    return {
        naver.BASIC_URL.format(code="353200"): _basic("353200", "140,000", "+1.82"),
        naver.BASIC_URL.format(code="003230"): _basic("003230", "1,200,000", "-0.50"),
        naver.NEWS_URL.format(code="353200"): _news(),
        naver.NEWS_URL.format(code="003230"): _news(),
    }


@pytest.mark.parametrize(
    ("utc", "session"),
    [
        ((2026, 10, 21, 1, 0), "intraday"),   # 수 10:00 KST — 장중, 가격선 판정은 미확정
        ((2026, 10, 21, 6, 45), "closed"),    # 수 15:45 KST — 정규장 종가 확정 뒤
        ((2026, 10, 20, 17, 32), "closed"),   # 수 02:32 KST — 개장 전, 전날 종가
        ((2026, 10, 24, 1, 0), "closed"),     # 토 10:00 KST
    ],
)
def test_session_says_whether_prices_are_settled_closes(monkeypatch, files, capsys, utc, session) -> None:
    """트리거는 종가로만 판정한다 — 15:40 KST 전 평일 장중가는 「미확정」으로 내보낸다."""
    from datetime import datetime, timezone

    from invagent.datafeed import cache

    monkeypatch.setattr(cache, "_now", lambda: datetime(*utc, tzinfo=timezone.utc).timestamp())
    monkeypatch.setattr(fsc, "load_key", lambda: None)
    monkeypatch.setattr(http, "read_url", _route(_quiet_routes()))

    assert brief_prep.main([*files, "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["session"] == session


def test_the_snapshot_rule_checks_are_passed_through(monkeypatch, files, capsys) -> None:
    monkeypatch.setattr(fsc, "load_key", lambda: None)
    monkeypatch.setattr(http, "read_url", _route(_quiet_routes()))

    assert brief_prep.main([*files, "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["rule_checks"] == [
        "매매규칙 6(-20% 전량 매도): 해당 없음",
        "매매규칙 11(24~30%↑ 익절 쿠션): 대덕전자 +37.50% (비중 4.2%)",
    ]
