"""`daily-digest/scripts/dart_scan.py` — 보유·관심 종목의 OpenDART 공시 목록 대조."""

import json
import time
from datetime import date, datetime, timezone

import pytest

from invagent.datafeed import cache, opendart, tickers
from tests.conftest import load_script

dart_scan = load_script("daily-digest", "dart_scan")

SNAPSHOT = """# 포트폴리오 스냅샷 — 2026-10-23

## 보유 (3종목 + 현금)

| 계좌 | 종목 | 섹터 | 보유 | 평단 | 현재가 | 매수금액 | 수익률 | 비중 | 평가금액 | 투자 아이디어 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 은행 | _현금 | 현금 | 1 | ₩100 | ₩100 | ₩100 | 0.00% | 50.0% | ₩100 |  |
| 삼성 | KODEX 200 | 지수 | 800 | ₩87,387 | ₩87,075 | ₩69,909,600 | -0.36% | 14.8% | ₩69,660,000 |  |
| 삼성 | 대덕전자 | AI | 150 | ₩100,000 | ₩137,500 | ₩15,000,000 | 37.50% | 4.2% | ₩20,625,000 |  |
| 삼성 | 삼성전자우 | AI/IDM | 430 | ₩167,631 | ₩166,200 | ₩72,081,330 | -0.85% | 15.2% | ₩71,466,000 |  |
"""

WATCHLIST = """# 관심 종목

- 삼양식품
- 대덕전자
"""

BREAKOUT = """# 돌파선 감시 리스트

> 머리말 표가 아니다 | 이 줄은 무시한다

## 감시 대상

| 종목 | 오늘 종가 | 상태 |
|---|---:|---|
| **GS건설** | 36,000 | 바닥 다지기 |
| 삼양식품 | 1,226,000 | 하락 중 |
| KODEX 전고체배터리ESS TOP2플러스 (0209D0) | 9,000 | 관망 |

## 조선·엔진 섹터 확인 (🚢)

| # | 종목 | 상태 |
|---|---|---|
| 1 | HD현대중공업 | 하락 중 |
"""

CODES = {
    "KODEX 200": "069500", "대덕전자": "353200", "삼성전자우": "005935", "삼양식품": "003230", "GS건설": "006360",
}


def filing(rcept_no, corp_name, stock_code, report_nm):
    return {
        "corp_code": "00000000",
        "corp_name": corp_name,
        "stock_code": stock_code,
        "corp_cls": "Y",
        "report_nm": report_nm,
        "rcept_no": rcept_no,
        "flr_nm": corp_name,
        "rcept_dt": rcept_no[:8],
        "rm": "",
    }


FILINGS = [
    filing("20261026000101", "대덕전자", "353200", "단일판매ㆍ공급계약체결"),
    filing("20261024000102", "삼성전자", "005930", "기업설명회(IR)개최(안내공시)"),
    filing("20261023000103", "삼양식품", "003230", "연결재무제표기준영업(잠정)실적(공정공시)"),
    filing("20261023000104", "하나금융지주", "086790", "자기주식취득결정"),
]


@pytest.fixture
def stub(monkeypatch):
    calls = []

    def fetch_filings(begin, end):
        calls.append((begin, end))
        return list(FILINGS), None

    def resolve_stock(query, overrides=None, *, stockeasy_fallback=True):
        assert stockeasy_fallback is False
        return {"stock_code": CODES[query], "stock_name": query, "exchange": None}, None, 0

    monkeypatch.setattr(opendart, "fetch_filings", fetch_filings)
    monkeypatch.setattr(tickers, "resolve_stock", resolve_stock)
    return calls


@pytest.fixture
def paths(tmp_path):
    snapshot = tmp_path / "snap.md"
    snapshot.write_text(SNAPSHOT, encoding="utf-8")
    watchlist = tmp_path / "watch.md"
    watchlist.write_text(WATCHLIST, encoding="utf-8")
    breakout = tmp_path / "breakout.md"
    breakout.write_text(BREAKOUT, encoding="utf-8")
    return {
        "tmp": tmp_path,
        "args": [
            "--snapshot", str(snapshot),
            "--watchlist", str(watchlist),
            "--breakout", str(breakout),
            "--archive", str(tmp_path / "archive"),
            "--raw", str(tmp_path / "raw.md"),
            "--links", str(tmp_path / "links"),
            "--asof", "2026-10-26",
        ],
    }


def run_json(capsys, argv) -> dict:
    assert dart_scan.main([*argv, "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def test_only_held_and_watched_companies_are_kept_preferred_shares_as_the_common(stub, paths, capsys) -> None:
    """삼성전자우 보유자는 삼성전자 공시를 받는다 — DART에 공시하는 것은 본주다."""
    result = run_json(capsys, paths["args"])

    assert [(f["name"], f["rcept_no"]) for f in result["filings"]] == [
        ("대덕전자", "20261026000101"),
        ("삼성전자우", "20261024000102"),
        ("삼양식품", "20261023000103"),
    ]
    assert [f["held"] for f in result["filings"]] == [True, True, False]


def test_breakout_watch_names_are_matched_too(stub, paths, capsys, monkeypatch) -> None:
    """`돌파선_감시.md` 「감시 대상」 표의 종목도 대조한다. 관심과 겹치면 관심으로 남는다.

    괄호 안 코드는 그대로 쓴다 — 신형 코드(0209D0)는 이름 검색이 안 된다. 섹터 그룹 표는 감시 대상이 아니다.
    """
    monkeypatch.setattr(
        opendart,
        "fetch_filings",
        lambda begin, end: (
            [
                filing("20261026000201", "GS건설", "006360", "단일판매ㆍ공급계약체결"),
                filing("20261026000202", "삼양식품", "003230", "자기주식취득결정"),
                filing("20261026000203", "HD현대중공업", "329180", "단일판매ㆍ공급계약체결"),
            ],
            None,
        ),
    )

    result = run_json(capsys, paths["args"])

    assert [(f["name"], f["source"]) for f in result["filings"]] == [("GS건설", "돌파선"), ("삼양식품", "관심")]
    assert result["gaps"] == []

    assert dart_scan.main(paths["args"]) == 0
    assert "[돌파선] GS건설 2026-10-26 단일판매ㆍ공급계약체결" in capsys.readouterr().out


def _briefing(root, day):
    path = root / day[:4] / day[5:7] / f"{day}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# 브리핑\n", encoding="utf-8")


def test_the_window_starts_on_the_last_briefing_day_before_today(stub, paths, capsys) -> None:
    """주말·건너뛴 날의 공시도 잡는다. 직전 브리핑일 자체도 포함한다 — 브리핑 뒤 저녁 공시가 있다.

    같은 날 다시 만드는 오늘 브리핑은 직전 브리핑이 아니다.
    """
    archive = paths["tmp"] / "archive"
    for day in ("2026-10-22", "2026-10-23", "2026-10-26"):
        _briefing(archive, day)

    result = run_json(capsys, paths["args"])

    assert stub == [(date(2026, 10, 23), date(2026, 10, 26))]
    assert result["since"] == "2026-10-23"


def test_a_long_gap_is_capped(stub, paths, capsys) -> None:
    """한 달 넘게 브리핑을 쉬었어도 수천 건을 페이지마다 받지 않는다 — 최근 14일까지만."""
    _briefing(paths["tmp"] / "archive", "2026-08-01")

    run_json(capsys, paths["args"])

    assert stub == [(date(2026, 10, 12), date(2026, 10, 26))]


def test_filings_already_forwarded_to_telegram_are_marked(stub, paths, capsys) -> None:
    """raw·링크 파일에 접수번호가 있으면 1-5단계 4) 보강 대상이다 — 새로 찾은 것만 「미수록」."""
    tmp = paths["tmp"]
    (tmp / "raw.md").write_text(
        "📁 단일판매ㆍ공급계약체결\n공시링크 : https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261026000101\n",
        encoding="utf-8",
    )
    (tmp / "links").mkdir()
    (tmp / "links" / "opendart_x.summary.md").write_text("접수번호 20261023000103\n", encoding="utf-8")

    result = run_json(capsys, paths["args"])

    assert {f["rcept_no"]: f["in_telegram"] for f in result["filings"]} == {
        "20261026000101": True,
        "20261024000102": False,
        "20261023000103": True,
    }


@pytest.fixture
def cet_evening_after_kst_midnight(monkeypatch, tmp_path):
    """2026-10-26 20:00 CET = 2026-10-27 04:00 KST. 브리핑을 만드는 사용자는 아직 10/26이다."""
    monkeypatch.setenv("TZ", "Europe/Berlin")
    time.tzset()
    monkeypatch.setattr(cache, "_now", lambda: datetime(2026, 10, 26, 19, 0, tzinfo=timezone.utc).timestamp())
    monkeypatch.setenv("INVAGENT_OUTPUT_DIR", str(tmp_path / "out"))
    yield tmp_path / "out" / "daily-digest"
    monkeypatch.undo()
    time.tzset()


def test_without_asof_the_raw_of_the_local_briefing_day_is_read(stub, paths, capsys, cet_evening_after_kst_midnight) -> None:
    """`fetch-messages`는 raw 이름을 로컬 날짜로 짓는다 — KST가 자정을 넘었다고 내일자 raw를 찾으면 수록 공시를 놓친다."""
    digest = cet_evening_after_kst_midnight
    (digest / "raw").mkdir(parents=True)
    (digest / "raw" / "2026-10-26_raw.md").write_text("공시링크 : rcpNo=20261026000101\n", encoding="utf-8")
    argv = [a for a in paths["args"] if a not in ("--asof", "2026-10-26")]
    argv = [a for i, a in enumerate(argv) if a not in ("--raw", "--links") and argv[i - 1] not in ("--raw", "--links")]

    result = run_json(capsys, argv)

    assert result["asof"] == "2026-10-26"
    assert {f["rcept_no"]: f["in_telegram"] for f in result["filings"]}["20261026000101"] is True


def test_without_asof_the_list_still_reaches_today_in_kst(stub, paths, capsys, cet_evening_after_kst_midnight) -> None:
    """DART 접수일은 KST다 — 로컬 날짜로 끊으면 KST 새벽(로컬 전날 저녁)에 접수된 공시가 빠진다."""
    argv = [a for a in paths["args"] if a not in ("--asof", "2026-10-26")]

    run_json(capsys, argv)

    assert stub[0][1] == date(2026, 10, 27)


@pytest.mark.parametrize(
    ("report_nm", "notable"),
    [
        ("단일판매ㆍ공급계약체결", True),
        ("[기재정정]연결재무제표기준영업(잠정)실적(공정공시)", True),
        ("주요사항보고서(유상증자결정)", True),
        ("주요사항보고서(자기주식취득결정)", True),
        ("주식등의대량보유상황보고서(일반)", True),
        ("투자판단관련주요경영사항(임상시험결과)", True),
        ("[기재정정]주식소각결정", True),
        ("풍문또는보도에대한해명(미확정)", True),
        ("기업설명회(IR)개최(안내공시)", False),
        ("임원ㆍ주요주주특정증권등소유상황보고서", False),
        ("분기보고서 (2026.09)", False),
    ],
)
def test_notable_filings_are_the_ones_that_move_a_decision(stub, paths, capsys, monkeypatch, report_nm, notable) -> None:
    """quick-brief의 특이 공시 기준과 같다 — 계약·증자·지분·실적·자사주·합병·이벤트는 주요, IR·정기·임원 소량은 일상."""
    monkeypatch.setattr(
        opendart, "fetch_filings", lambda begin, end: ([filing("20261026000101", "대덕전자", "353200", report_nm)], None)
    )

    result = run_json(capsys, paths["args"])

    assert result["filings"][0]["notable"] is notable


def test_text_output_leads_with_notable_filings_telegram_missed(stub, paths, capsys) -> None:
    (paths["tmp"] / "raw.md").write_text("rcpNo=20261026000101\n", encoding="utf-8")

    assert dart_scan.main(paths["args"]) == 0
    out = capsys.readouterr().out

    missed = out.index("텔레그램 미수록 주요 1건")
    assert "[관심] 삼양식품 2026-10-23 연결재무제표기준영업(잠정)실적(공정공시) · 20261023000103" in out[missed:]
    assert "텔레그램 수록 1건" in out and "대덕전자 단일판매ㆍ공급계약체결 · 20261026000101" in out
    assert "일상 공시 1건" in out and "삼성전자우 기업설명회(IR)개최(안내공시)" in out


def test_a_failed_list_fetch_exits_1_with_the_reason(stub, paths, capsys, monkeypatch) -> None:
    """빈 목록으로 출력하면 「공시 없음」으로 읽힌다."""
    monkeypatch.setattr(opendart, "fetch_filings", lambda begin, end: (None, "OpenDART 020 요청 제한"))

    assert dart_scan.main(paths["args"]) == 1
    assert "OpenDART 020 요청 제한" in capsys.readouterr().err
