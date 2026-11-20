"""`quick-brief/scripts/brief_prep.py` — 보유·관심 종목 시세·뉴스 수집 (StockEasy 미사용)."""

import json
from datetime import date, timedelta

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

- 매매 규칙 6(-20% 전량 매도): 해당 없음
- 매매 규칙 10(24~30%↑ 익절 쿠션): 대덕전자 +37.50% (비중 4.2%)

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
    triggers = tmp_path / "triggers.md"
    triggers.write_text("# 트리거 없음\n", encoding="utf-8")
    return ["--snapshot", str(snapshot), "--watchlist", str(watchlist), "--triggers", str(triggers),
            "--asof", "2026-10-20"]


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
        "매매 규칙 6(-20% 전량 매도): 해당 없음",
        "매매 규칙 10(24~30%↑ 익절 쿠션): 대덕전자 +37.50% (비중 4.2%)",
    ]


def test_watched_and_trigger_stocks_are_listed_by_change_high_to_low(monkeypatch, files, tmp_path, capsys) -> None:
    """관심·트리거 종목은 등락률 내림차순, 미수집은 끝. 보유는 등락과 무관하게 앞에서 제 순서를 지킨다."""
    triggers = tmp_path / "triggers.md"
    triggers.write_text("## 이수페타시스 (007660)\n\n## 기가비스 (420770)\n", encoding="utf-8")
    monkeypatch.setattr(fsc, "load_key", lambda: None)
    monkeypatch.setattr(http, "read_url", _route({
        naver.BASIC_URL.format(code="353200"): _basic("353200", "140,000", "-2.00"),
        naver.BASIC_URL.format(code="003230"): _basic("003230", "1,200,000", "-0.50"),
        naver.BASIC_URL.format(code="420770"): b'{"stockName":"X"}',
        naver.BASIC_URL.format(code="007660"): _basic("007660", "130,000", "+3.10"),
        naver.NEWS_URL.format(code="353200"): _news(),
        naver.NEWS_URL.format(code="003230"): _news(),
        naver.NEWS_URL.format(code="420770"): _news(),
        naver.NEWS_URL.format(code="007660"): _news(),
    }))

    assert brief_prep.main([*files, "--json"]) == 0

    names = [s["name"] for s in json.loads(capsys.readouterr().out)["stocks"]]
    assert names == ["대덕전자", "이수페타시스", "삼양식품", "기가비스"]


def _index_bars(closes: list[float], last: date) -> list[dict]:
    """평일만 거꾸로 세어 `last`에서 끝나는 지수 일봉."""
    days, day = [], last
    while len(days) < len(closes):
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return [
        {"date": d.strftime("%Y%m%d"), "open": c, "high": c, "low": c, "close": c, "volume": 1.0}
        for d, c in zip(reversed(days), closes)
    ]


def _index_quote(price: float, day: str = "2026-11-13") -> dict:
    return {"name": "코스피", "price": price, "change": 0.0, "change_pct": 0.0,
            "market_status": "OPEN", "traded_at": f"{day}T10:00:00+09:00"}


def test_index_view_appends_today_from_the_quote_and_reads_averages_and_regime() -> None:
    # 52주 고점 125에서 104까지 -16.8% → 조정. 오늘 값은 일봉이 아니라 현재가에서 온다.
    closes = [100.0] * 240 + [125, 120, 115, 112, 110, 108, 107, 106, 105]
    bars = _index_bars(closes, date(2026, 11, 12))

    view = brief_prep.index_view(bars, _index_quote(104.0), date(2026, 11, 13))

    assert view["close"] == 104.0
    assert view["above"] == {3: False, 20: False, 200: True}
    assert view["drawdown"] == pytest.approx(-16.8)
    assert view["regime"] == "correction"
    assert view["ma20_cross"] is None
    assert view["leverage_rule3"] == "ok"


def test_index_view_calls_a_crash_on_a_deep_three_day_slide() -> None:
    # 고점 100 → 86(-14%), 오늘 -4.4%, 3일 누적 -14% → 급락.
    bars = _index_bars([100.0] * 240 + [95, 90], date(2026, 11, 12))

    view = brief_prep.index_view(bars, _index_quote(86.0), date(2026, 11, 13))

    assert view["regime"] == "crash"


def test_index_view_flags_the_day_the_close_falls_through_the_20_day_line() -> None:
    bars = _index_bars([100.0] * 248 + [101], date(2026, 11, 12))

    view = brief_prep.index_view(bars, _index_quote(98.0), date(2026, 11, 13))

    assert view["ma20_cross"] == "down"
    assert view["regime"] == "normal"


def test_index_view_fires_leverage_rule_three_on_three_wide_days_without_holding_the_low() -> None:
    bars = _index_bars([100.0] * 240 + [94, 100, 93, 90, 89, 88, 87, 86, 85], date(2026, 11, 12))

    view = brief_prep.index_view(bars, _index_quote(84.0), date(2026, 11, 13))

    assert view["leverage_rule3"] == "fired"


def test_index_view_ignores_a_quote_from_another_day() -> None:
    bars = _index_bars([100.0] * 249 + [104], date(2026, 11, 13))

    view = brief_prep.index_view(bars, _index_quote(90.0, day="2026-11-12"), date(2026, 11, 13))

    assert view["close"] == 104.0


def _sise(closes: list[float], last: date) -> bytes:
    rows = ["['날짜','시가','고가','저가','종가','거래량','외국인소진율']"] + [
        f"['{b['date']}',{b['close']},{b['close']},{b['close']},{b['close']},1,0.0]"
        for b in _index_bars(closes, last)
    ]
    return ("[" + ",".join(rows) + "]").encode()


def _index_basic(price: str, pct: str, day: str) -> bytes:
    return json.dumps(
        {"stockName": "지수", "closePrice": price, "compareToPreviousClosePrice": "0",
         "fluctuationsRatio": pct, "marketStatus": "CLOSE", "localTradedAt": f"{day}T16:00:00+09:00"}
    ).encode()


def test_main_reports_both_indices(monkeypatch, files, capsys) -> None:
    monkeypatch.setattr(fsc, "load_key", lambda: None)
    monkeypatch.setattr(http, "read_url", _route({
        **_quiet_routes(),
        f"{naver.SISE_URL}?symbol=KOSPI&": _sise([100.0] * 249, date(2026, 10, 19)),
        f"{naver.SISE_URL}?symbol=KOSDAQ&": _sise([100.0] * 249, date(2026, 10, 19)),
        naver.INDEX_BASIC_URL.format(code="KOSPI"): _index_basic("99.00", "-1.00", "2026-10-20"),
        naver.INDEX_BASIC_URL.format(code="KOSDAQ"): _index_basic("101.00", "+1.00", "2026-10-20"),
    }))

    assert brief_prep.main([*files, "--json"]) == 0

    market = json.loads(capsys.readouterr().out)["market"]
    assert market["indices"]["KOSPI"]["above"]["3"] is False
    assert market["indices"]["KOSDAQ"]["above"]["3"] is True
    assert market["indices"]["KOSDAQ"]["change_pct"] == 1.0
    assert "account_rule1_released" not in market


def test_text_output_puts_the_market_table_first(monkeypatch, files, capsys) -> None:
    monkeypatch.setattr(fsc, "load_key", lambda: None)
    monkeypatch.setattr(http, "read_url", _route({
        **_quiet_routes(),
        f"{naver.SISE_URL}?symbol=KOSPI&": _sise([100.0] * 249, date(2026, 10, 19)),
        f"{naver.SISE_URL}?symbol=KOSDAQ&": TimeoutError("timed out"),
        naver.INDEX_BASIC_URL.format(code="KOSPI"): _index_basic("99.00", "-1.00", "2026-10-20"),
    }))

    assert brief_prep.main(files) == 0

    out = capsys.readouterr().out
    assert "| KOSPI | 99.00 | -1.00% | ❌ | ❌ | ❌ | -1.0% | 정상 |" in out
    assert out.index("| KOSPI |") < out.index("| 보유 | 대덕전자 |")
    assert "계좌 규칙 1" not in out  # 3일선 열이 이미 보여 준다
    assert "KOSDAQ 일봉:" in out


BUY_TRIGGERS = """# 매수 트리거

## 원익QnC (074600) — 새 고개 들기 대기

- 종가 28,000원 이하

## 감시 대상

| 종목 | 오늘 종가 | 상태 |
|---|---:|---|
| LG씨엔에스(064400) | 80,100 | 고개 들기 |
| 대덕전자 | 93,400 | 하락 중 |

## 조선·엔진 섹터 확인 (🚢)

## 삼양식품 (003230) — 이미 관심 목록에 있다
"""


def test_trigger_file_stocks_are_quoted_by_their_written_ticker(monkeypatch, files, tmp_path, capsys) -> None:
    """트리거 점검 범위 = 보유·매수 트리거 파일의 종목. 적힌 티커로 받고 이름 검색을 타지 않는다."""
    triggers = tmp_path / "buy.md"
    triggers.write_text(BUY_TRIGGERS, encoding="utf-8")
    args = [*files[:-4], "--triggers", str(triggers), "--asof", "2026-10-20", "--json"]
    monkeypatch.setattr(fsc, "load_key", lambda: None)
    monkeypatch.setattr(http, "read_url", _route({
        **_quiet_routes(),
        naver.BASIC_URL.format(code="074600"): _basic("074600", "29,000", "+1.00"),
        naver.BASIC_URL.format(code="064400"): _basic("064400", "80,000", "-1.00"),
        naver.NEWS_URL.format(code="074600"): _news(),
        naver.NEWS_URL.format(code="064400"): _news(),
    }))

    assert brief_prep.main(args) == 0

    stocks = {s["name"]: s for s in json.loads(capsys.readouterr().out)["stocks"]}
    assert set(stocks) == {"대덕전자", "삼양식품", "원익QnC", "LG씨엔에스"}
    assert (stocks["원익QnC"]["code"], stocks["원익QnC"]["price"]) == ("074600", 29000.0)
    assert stocks["LG씨엔에스"]["trigger"] is True and stocks["LG씨엔에스"]["held"] is False
    assert stocks["삼양식품"]["trigger"] is True
    assert stocks["대덕전자"]["trigger"] is True and stocks["대덕전자"]["held"] is True


def test_text_output_labels_trigger_only_stocks(monkeypatch, files, tmp_path, capsys) -> None:
    triggers = tmp_path / "buy.md"
    triggers.write_text("## 원익QnC (074600) — 대기\n", encoding="utf-8")
    args = [*files[:-4], "--triggers", str(triggers), "--asof", "2026-10-20"]
    monkeypatch.setattr(fsc, "load_key", lambda: None)
    monkeypatch.setattr(http, "read_url", _route({
        **_quiet_routes(),
        naver.BASIC_URL.format(code="074600"): _basic("074600", "29,000", "+1.00"),
        naver.NEWS_URL.format(code="074600"): _news(),
    }))

    assert brief_prep.main(args) == 0

    out = capsys.readouterr().out
    assert "| 트리거 | 원익QnC | 29,000 |" in out
    assert "| 관심 | 삼양식품 |" in out


def test_regime_flags_a_crash_on_a_single_five_percent_day_after_a_rebound() -> None:
    """하루 -5% 이하는 3일 누적과 무관하게 급락이다 (2026-11-20 개정 — daily-digest와 같은 정의)."""
    # 고점 100 → 조정권 90 → 92 → 94 → 89(-5.3%) · 3일 누적 -1.1%
    closes = [100.0] * 10 + [90.0, 92.0, 94.0, 89.0]

    assert brief_prep.regime(closes)[0] == "crash"
