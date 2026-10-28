"""daily-digest `extract_earnings.py` — 실적 공시 → 실적정리 시트 셀."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import load_script

# --- extract_earnings.py (실적 공시 → 실적정리 시트 셀) -----------------------------

# raw export 그대로의 모양: 메시지마다 `**[포워드]** 날짜` 머리가 붙고, 공시 봇 본문은
# 회사 줄 · `📁 공시명` · 공시 시각 순서다. 숫자는 AWAKE PRO 실적발표 채널의 실제 샘플.
EARNINGS_RAW_LG_PRELIM = """**[포워드]** 2026-10-07 07:24
🔵 LG전자(시가총액 : 35.2조)
📁 연결재무제표기준영업(잠정)실적(공정공시)
2026.10.07 11:00

매출액 : 238,270억(예상치 : 242,501억, -1.7%)
영업익 : 7,818억(예상치 : 10,391억, -24.8%)
순이익 : -

* 최근 실적
(기간/ 매출/ 영업익/ 순익)
2026.2Q : 238,270억/ 7,818억/ -
2026.1Q : 238,265억/ 15,791억/ 7,813억
2025.4Q : 237,272억/ 16,737억/ 10,051억
2025.3Q : 238,522억/ -1,090억/ -7,259억
2025.2Q : 218,738억/ 6,889억/ 4,611억

- 어닝 쇼크(-24.8%)


공시링크 : https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261007800145
회사정보 : https://finance.naver.com/item/main.naver?code=066570

**[포워드]** 2026-10-07 07:26
[리브스메드] 다올투자증권 「수술로봇은 어떻게 시장이 되었나」
"""


def test_extract_earnings_parses_consolidated_preliminary_with_consensus() -> None:
    module = load_script("daily-digest", "extract_earnings")

    entries = module.parse_earnings(EARNINGS_RAW_LG_PRELIM)

    assert len(entries) == 1
    entry = entries[0]
    assert entry["name"] == "LG전자"
    assert entry["code"] == "066570"
    assert entry["kind"] == "잠정"
    assert entry["basis"] == "연결"
    # 봇의 「최근 실적」 첫 줄은 2026.2Q로 찍혔지만 10월 공시는 3분기 실적이다.
    assert entry["tab"] == "3q26"
    assert entry["revenue"] == {"actual": 238270, "consensus": 242501}
    assert entry["op"] == {"actual": 7818, "consensus": 10391}
    assert entry["rcp_no"] == "20261007800145"


EARNINGS_RAW_CONFIRMED = """**[포워드]** 2026-08-14 09:38
🔴 E1(시가총액 : 5,899억)
📁 반기보고서 (2026.06)
2026.08.14 18:38

잠정실적 : N

매출액 : 44,028억 (예상치 : 38,222억/ +15.2%)
영업익 : 1,179억 (예상치 : 933억/ +26.4%)
순이익 : 993억

공시링크 : https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260814004295
회사정보 : https://finance.naver.com/item/main.naver?code=017940

**[포워드]** 2026-10-07 07:11
📌 본느(시가총액 : 489억)
📁 반기보고서 (2026.06)
2026.10.07 16:11

잠정실적 : N

매출액 : 85억(예상치 : -)
영업익 : -23억(예상치 : -)
순이익 : -94억

공시링크 : https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261007000362

**[포워드]** 2026-07-15 06:45
📌 풍강(시가총액 : 250억)
📁 분기보고서 (2026.05)
2026.07.15 15:45

매출액 : 230억
영업익 : 15억

공시링크 : https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260715000353
"""


def test_extract_earnings_keeps_an_alphanumeric_krx_code() -> None:
    """신형 KRX 코드(에임드바이오 `0009K0`)도 회사정보 링크에서 그대로 읽는다."""
    module = load_script("daily-digest", "extract_earnings")
    raw = EARNINGS_RAW_LG_PRELIM.replace("code=066570", "code=0009K0")

    entries = module.parse_earnings(raw)

    assert entries[0]["code"] == "0009K0"


def test_extract_earnings_takes_confirmed_reports_by_their_period() -> None:
    module = load_script("daily-digest", "extract_earnings")

    e1, bonne, punggang = module.parse_earnings(EARNINGS_RAW_CONFIRMED)

    assert e1["kind"] == "확정"
    assert e1["tab"] == "2q26"
    # `(예상치 : X억/ +Y%)` 형식도 같은 값으로 읽는다.
    assert e1["revenue"] == {"actual": 44028, "consensus": 38222}
    assert e1["op"] == {"actual": 1179, "consensus": 933}
    # 10월에 늦게 낸 반기보고서도 공시일이 아니라 보고 기간(2026.06)으로 탭을 정한다.
    assert bonne["tab"] == "2q26"
    assert bonne["op"] == {"actual": -23, "consensus": None}
    # 결산월이 분기 말(3·6·9·12월)이 아니면 어느 탭인지 정할 수 없어 쓰지 않는다.
    assert punggang["tab"] is None
    assert "2026.05" in punggang["skip_reason"]


def test_extract_earnings_prefers_consolidated_over_separate_for_the_same_quarter() -> None:
    module = load_script("daily-digest", "extract_earnings")
    separate = """**[포워드]** 2026-10-07 07:30
📌 LG전자(시가총액 : 35.2조)
📁 영업(잠정)실적(공정공시)
2026.10.07 11:02

구분 : 개별실적

매출액 : 99,000억
영업익 : 7,017억

공시링크 : https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261007800199
"""

    # 개별이 먼저 오든 나중에 오든 연결 한 건만 남는다.
    for raw in (separate + EARNINGS_RAW_LG_PRELIM, EARNINGS_RAW_LG_PRELIM + separate):
        entries = module.parse_earnings(raw)
        assert [(e["name"], e["basis"]) for e in entries] == [("LG전자", "연결")]


EARNINGS_RAW_CHANGE = """**[포워드]** 2026-09-01 07:40
📌 세원정공(시가총액 : 1,062억)
📁 매출액또는손익구조30%(대규모법인은15%)이상변경
2026.09.01 16:40
재무제표 종류 : 연결

매출액 : 584억(예상치 : -)
영업익 : 113억(예상치 : -)
순이익 : 261억(예상치 : -)

최근 실적 추이
매출/영업익/순익/예상대비 OP
2026.2Q 584억/ 113억/ 261억
2026.1Q 384억/ -7억/ 20억

공시링크 : https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260901800554

**[포워드]** 2026-03-05 05:58
📌 양지사(시가총액 : 665억)
📁 매출액또는손익구조30%(대규모법인은15%)이상변동
2026.03.05 14:58



(연간)매출액 : 495억(연간)
영업익 : -64억(연간)
순이익 : -26억(연간)

매출감소 및 원재료비 상승 등으로 인한 비용증가

- 최근 5개분기 최소 영업익


공시링크 : https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260305900652
회사정보 : https://finance.naver.com/item/main.naver?code=030960
"""


def test_extract_earnings_reads_both_shapes_of_the_30_percent_change_filing() -> None:
    module = load_script("daily-digest", "extract_earnings")

    quarterly, annual = module.parse_earnings(EARNINGS_RAW_CHANGE)

    # 분기 값을 바로 싣는 형식은 잠정처럼 공시일로 탭을 정한다 (9월 공시 → 2분기).
    assert quarterly["kind"] == "변동"
    assert quarterly["annual"] is False
    assert quarterly["tab"] == "2q26"
    assert quarterly["op"] == {"actual": 113, "consensus": None}
    # 연간 값만 싣는 형식은 앞선 세 분기를 빼야 분기 값이 나온다 — 탭과 분기 값은 아직 없다.
    assert annual["kind"] == "변동"
    assert annual["annual"] is True
    assert annual["tab"] is None
    assert annual["revenue"] == {"actual": 495, "consensus": None}
    assert annual["op"] == {"actual": -64, "consensus": None}
    assert annual["code"] == "030960"


def test_extract_earnings_sheet_cells_for_a_stock_with_consensus() -> None:
    module = load_script("daily-digest", "extract_earnings")
    entry = module.parse_earnings(EARNINGS_RAW_LG_PRELIM)[0]

    cells = module.sheet_cells(entry)

    # 서프율은 시트의 기존 값과 같은 모양(소수 1자리 %, 양수에 + 없음)이다.
    assert cells["revenue_surprise"] == "-1.7%"
    assert cells["op_surprise"] == "-24.8%"
    # 변화율은 「최근 실적」 표의 위치로 잰다 — 첫 줄 vs 둘째 줄이 QoQ, 다섯째 줄이 YoY.
    # DART 원문: 매출 YoY +8.9% QoQ 0.0%, 영업이익 YoY +13.5% QoQ -50.5%.
    assert cells["note"] == "[잠정] 매출 yoy +8.9% qoq +0.0%, 영익 yoy +13.5% qoq -50.5%"


EARNINGS_RAW_NO_CONSENSUS = """**[포워드]** 2026-09-01 07:40
📌 세원정공(시가총액 : 1,062억)
📁 매출액또는손익구조30%(대규모법인은15%)이상변경
2026.09.01 16:40
재무제표 종류 : 연결

매출액 : 584억(예상치 : -)
영업익 : 113억(예상치 : -)

최근 실적 추이
매출/영업익/순익/예상대비 OP
2026.2Q 584억/ 113억/ 261억
2026.1Q 384억/ -7억/ 20억
2025.4Q 420억/ 51억/ 140억
2025.3Q 392억/ 79억/ 139억
2025.2Q 606억/ 81억/ 161억

- 최근 5개분기 최대 영업익
- 최근 5개분기 최대 영업이익률(19.3%)

공시링크 : https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260901800554

**[포워드]** 2026-10-07 07:11
📌 본느(시가총액 : 489억)
📁 반기보고서 (2026.06)
2026.10.07 16:11

매출액 : 85억(예상치 : -)
영업익 : -23억(예상치 : -)

* 최근 실적
(기간/ 매출/ 영업익/ 순익)
2026.2Q 85억/ -23억/ -94억
2026.1Q 93억/ -21억/ -19억
2025.4Q 86억/ -26억/ -97억
2025.3Q 120억/ -17억/ -23억
2025.2Q 123억/ -26억/ -11억

공시링크 : https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261007000362
"""


def test_extract_earnings_sheet_cells_without_consensus_leave_only_a_note() -> None:
    module = load_script("daily-digest", "extract_earnings")
    sewon, bonne = module.parse_earnings(EARNINGS_RAW_NO_CONSENSUS)

    sewon_cells = module.sheet_cells(sewon)
    bonne_cells = module.sheet_cells(bonne)

    assert sewon_cells["revenue_surprise"] == "" and sewon_cells["op_surprise"] == ""
    # 손실에서 이익으로 바뀐 쪽은 %가 뜻이 없어 전환으로 적는다. 5분기 최대·최소는 표에서
    # 직접 재고, 봇 태그 중 그것과 겹치는 「최대 영업익」은 빼고 나머지는 덧붙인다.
    assert sewon_cells["note"] == (
        "[변동] 5분기 내 최대 영익, 매출 yoy -3.6% qoq +52.1%, 영익 yoy +39.5% qoq 흑자 전환, "
        "최근 5개분기 최대 영업이익률(19.3%)"
    )
    assert bonne_cells["note"] == (
        "[확정] 5분기 내 최소 매출, 매출 yoy -30.9% qoq -8.6%, 영익 yoy 적자 축소 qoq 적자 확대"
    )


def test_extract_earnings_spells_out_amounts_when_a_loss_is_involved() -> None:
    module = load_script("daily-digest", "extract_earnings")
    raw = """**[포워드]** 2026-07-30 07:24
🔵 삼성SDI(시가총액 : 20.1조)
📁 연결재무제표기준영업(잠정)실적(공정공시)
2026.07.30 08:00

매출액 : 39,000억(예상치 : 37,537억, +3.9%)
영업익 : 2,038억(예상치 : -352억, +678.3%)

공시링크 : https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260730800001
"""
    entry = module.parse_earnings(raw)[0]

    cells = module.sheet_cells(entry)

    # 예상이 손실이면 %만으로는 읽을 수 없어 시트의 기존 메모처럼 두 금액을 적는다.
    # 분모는 |예상|이라 손실 예상에서 이익이 나면 양수다: (2038 + 352) / 352.
    assert cells["op_surprise"] == "679.0%"
    assert cells["note"] == "[잠정] 컨센 -352억, 영익 2038억"


def _fs_row(year: int, month: int, revenue_eok: int, op_eok: int, quarter: int | None = None) -> dict:
    # StockEasy `info-tab` financials 행 모양: 금액은 원 단위, month는 기간 말 월.
    return {
        "year": year, "month": month, "quarter": quarter,
        "revenue": revenue_eok * 100_000_000, "operating_income": op_eok * 100_000_000,
    }


def test_extract_earnings_derives_the_fourth_quarter_from_an_annual_change_filing() -> None:
    module = load_script("daily-digest", "extract_earnings")
    annual = module.parse_earnings(EARNINGS_RAW_CHANGE)[1]  # 양지사, 2026-03-05, 연간 495억/-64억
    quarters = [
        _fs_row(2025, 9, 120, -20, 3),
        _fs_row(2025, 6, 125, -15, 2),
        _fs_row(2025, 3, 130, -10, 1),
        _fs_row(2024, 12, 140, 5, 4),
    ]

    entry = module.derive_quarter(annual, quarters, [_fs_row(2024, 12, 520, 12)])

    # 12월 결산이 3월에 낸 연간 공시 → 2025년 4분기. 연간에서 1~3분기 합을 뺀다.
    assert entry["tab"] == "4q25"
    assert entry["revenue"] == {"actual": 495 - 375, "consensus": None}
    assert entry["op"] == {"actual": -64 - (-45), "consensus": None}
    assert module.sheet_cells(entry)["note"] == (
        "[변동] 5분기 내 최소 매출, 매출 yoy -14.3% qoq +0.0%, 영익 yoy 적자 전환 qoq 적자 축소, "
        "연간 매출 495억·영익 -64억에서 1~3분기 합을 빼 계산"
    )


def test_extract_earnings_derives_by_fiscal_year_end_and_skips_when_a_quarter_is_missing() -> None:
    module = load_script("daily-digest", "extract_earnings")
    raw = """**[포워드]** 2026-06-19 08:13
📌 동원금속(시가총액 : 841억)
📁 매출액또는손익구조30%(대규모법인은15%)이상변경
2026.06.19 17:13

(연간)매출액 : 6,594억(연간)
영업익 : 357억(연간)

- 최근 5개분기 최대 영업이익률(9.1%)

공시링크 : https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260619800917
회사정보 : https://finance.naver.com/item/main.naver?code=018500
"""
    annual = module.parse_earnings(raw)[0]
    quarters = [
        _fs_row(2025, 12, 1571, 74),
        _fs_row(2025, 9, 1556, 85),
        _fs_row(2025, 6, 1680, 131),
    ]
    yearly = [_fs_row(2025, 3, 6000, 300)]  # 3월 결산

    entry = module.derive_quarter(annual, quarters, yearly)
    # 6월에 낸 3월 결산 연간 공시 → 2026.03으로 끝난 분기 = 1q26.
    assert entry["tab"] == "1q26"
    assert entry["op"]["actual"] == 357 - (74 + 85 + 131)
    # 연간형에 붙은 봇 태그는 연간 기준인지 분기 기준인지 알 수 없어 옮기지 않는다.
    assert "영업이익률" not in module.sheet_cells(entry)["note"]

    missing = module.derive_quarter(annual, quarters[:2], yearly)
    assert missing["tab"] is None
    assert "2025.06" in missing["skip_reason"]


def test_extract_earnings_main_prints_sheet_rows_and_fetches_only_for_annual_filings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from invagent.datafeed import http

    module = load_script("daily-digest", "extract_earnings")
    monkeypatch.setenv("STOCKEASY_COOKIE", "session=x")
    seen: list[str] = []
    info_tab = {
        "primary_fs_type": "C",
        "financials": {
            "consolidated": [
                _fs_row(2025, 9, 120, -20, 3),
                _fs_row(2025, 6, 125, -15, 2),
                _fs_row(2025, 3, 130, -10, 1),
            ],
            "consolidatedYearly": [_fs_row(2024, 12, 520, 12)],
        },
    }

    def read_url(url, headers, timeout):
        seen.append(url)
        assert "/stock-info/info-tab/030960" in url
        return json.dumps(info_tab).encode()

    monkeypatch.setattr(http, "read_url", read_url)
    raw = tmp_path / "2026-10-07_raw.md"
    raw.write_text(EARNINGS_RAW_LG_PRELIM + EARNINGS_RAW_CHANGE, encoding="utf-8")

    exit_code = module.main([str(raw)])

    assert exit_code == 0
    assert len(seen) == 1
    out = json.loads(capsys.readouterr().out)
    rows = {row["name"]: row for row in out["rows"]}
    assert rows["LG전자"] == {
        "name": "LG전자", "tab": "3q26", "kind": "잠정",
        "revenue_surprise": "-1.7%", "op_surprise": "-24.8%",
        "note": "[잠정] 매출 yoy +8.9% qoq +0.0%, 영익 yoy +13.5% qoq -50.5%",
        "rcp_no": "20261007800145",
    }
    assert rows["세원정공"]["tab"] == "2q26"
    assert rows["양지사"]["tab"] == "4q25"
    assert out["skipped"] == []


def test_extract_earnings_keeps_table_rows_aligned_when_a_value_is_blank() -> None:
    module = load_script("daily-digest", "extract_earnings")
    raw = """**[포워드]** 2026-08-14 09:38
📌 다원시스(시가총액 : 3,000억)
📁 반기보고서 (2026.06)
2026.08.14 18:38

매출액 : -
영업익 : -229억

* 최근 실적
(기간/ 매출/ 영업익/ 순익)
2026.2Q -/ -229억/ -250억
2026.1Q 210억/ -95억/ 343억
2025.4Q 47억/ -631억/ -1,389억
2025.3Q 280억/ -193억/ -189억
2025.2Q 355억/ -241억/ -196억

공시링크 : https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260814004290
"""
    entry = module.parse_earnings(raw)[0]

    # 매출 칸이 비어도 첫 줄을 건너뛰지 않는다 — 건너뛰면 직전 분기가 이번 분기 자리에 온다.
    assert module.sheet_cells(entry)["note"] == "[확정] 영익 yoy 적자 축소 qoq 적자 확대"
