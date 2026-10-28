"""daily-digest `extract_portfolio.py` — 포트폴리오 시트 파싱과 룰 판정."""

from __future__ import annotations

import base64
import csv
import io
import json
from pathlib import Path

import pytest

from tests.conftest import load_script

PORTFOLIO_DUMP = """|  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |
| :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| 구분 |  계좌 | 섹터 | 종목 | 보유 | 평단 | 현재가 | 매수금액 | 평가금액 | 수익률 | 손익 | 비중 |  | 잔고 | ₩210,220,000 | 수익률 | 25.53% |
| B | 삼성 | 반도체 | 알파전자 | 95 | ₩703,575 | ₩1,466,000 | ₩66,839,625 | ₩139,270,000 | 108.36% | ₩72,430,375 | 26.3% | 슈퍼 사이클 |  |  |  |  |
| B | 삼성 | 바이오 | 베타파마 | 900 | ₩68,845 | ₩57,500 | ₩61,960,500 | ₩51,750,000 | \\-16.48% | \\-₩10,210,500 | 9.8% | 신약 |  |  |  |  |
| C | 삼성 | 소비재 | 감마엔터 | 500 | ₩30,278 | ₩28,400 | ₩15,139,000 | ₩14,200,000 | \\-9.20% | \\-₩939,000 | 2.7% | 신작 |  |  |  |  |
| 0 | 은행 | 현금 | \\_현금 | 1 | ₩5,000,000 | ₩5,000,000 | ₩5,000,000 | ₩5,000,000 | 0.00% | ₩0 | 5.1% | 현금도 종목이다 |  |  |  |  |

|  |  |  |  |  |
| :-: | :-: | :-: | :-: | :-: |
|  | 섹터 | 비중 | 평가금액 | 수익률 |
|  | 반도체 | 42.3% | ₩139,270,000 | 108.36% |
|  | 바이오 | 24.1% | ₩51,750,000 | \\-16.48% |
|  |  | 0.0% | ₩0 | \\#DIV/0\\! |

|  |  |  |  |
| :-: | :-: | :-: | :-: |
| 종목 | 최초 투자 | 투자 종료 | 수익률 |
| 델타중공업 | 2024-01-02 | 2024-05-05 | 31.00% |
"""


def test_portfolio_parser_extracts_first_sheet_only() -> None:
    module = load_script("daily-digest", "extract_portfolio")
    snapshot = module.build_snapshot(PORTFOLIO_DUMP, "2026-08-09")

    assert "# 포트폴리오 스냅샷 — 2026-08-09" in snapshot
    assert "잔고 ₩210,220,000 · 수익률 25.53%" in snapshot
    assert "## 보유 (3종목 + 현금)" in snapshot
    # 다음 시트(매매기록)는 잘라낸다
    assert "델타중공업" not in snapshot
    assert "최초 투자" not in snapshot
    # 섹터 표는 유지하되 빈 행·#DIV/0! 행은 버린다
    assert "| 반도체 | 42.3% | ₩139,270,000 | 108.36% |" in snapshot
    assert "DIV/0" not in snapshot


def test_portfolio_parser_unescapes_and_flags_rules() -> None:
    module = load_script("daily-digest", "extract_portfolio")
    snapshot = module.build_snapshot(PORTFOLIO_DUMP, "2026-08-09")

    assert "-16.48%" in snapshot and "\\-16.48%" not in snapshot
    assert "| _현금 | 현금 |" in snapshot

    assert "매매규칙 6(-15% 전량 매도): 베타파마 -16.48% (비중 9.8%) ← **위반**" in snapshot
    assert "매매규칙 6(-12% 1차 분할 매도): 해당 없음" in snapshot
    # -9.20%는 「매매규칙 7」의 -8% 선을 넘었다.
    assert "매매규칙 7(-8% 비중 축소 고려): 감마엔터 -9.20% (비중 2.7%)" in snapshot
    assert "매매규칙 11(24~30%↑ 익절 쿠션): 알파전자 +108.36% (비중 26.3%)" in snapshot
    assert "매매규칙 9(평가 비중 35% 상한): 해당 없음" in snapshot
    # 현금은 종목 수·룰 판정에서 제외
    assert "매매규칙 9(종목 수 5~12): 3종목 ← **미달**" in snapshot
    assert "현금 비중: 5.1% (₩5,000,000)" in snapshot


PORTFOLIO_CSV = (
    "구분, 계좌,섹터,종목,보유,평단,현재가,매수금액,평가금액,수익률,손익,비중,,잔고,\"₩21,000,000\",총손익,\"₩1,000,000\",투자금,\"₩20,000,000\",수익률,5.00%\r\n"
    "0,은행,현금,_현금,1,\"₩5,000,000\",\"₩5,000,000\",\"₩5,000,000\",\"₩5,000,000\",0.00%,₩0,23.8%,현금도 종목이다,,,,,,,,\r\n"
    "A,삼성,반도체,알파전자,10,\"₩1,000,000\",\"₩1,100,000\",\"₩10,000,000\",\"₩11,000,000\",10.00%,\"₩1,000,000\",52.4%,\"슈퍼 사이클, 비중 30% 실현\",,,,,,,,\r\n"
    "B,삼성,기판,오메가기판,50,\"₩100,000\",\"₩100,000\",\"₩5,000,000\",\"₩5,000,000\",-0.00%,₩0,23.8%,\"증설, 저평가\",,,,,,,,"
)


def _drive_download_response(csv_text: str) -> str:
    """Drive MCP `download_file_content`(exportMimeType=text/csv) 응답을 그대로 저장한 모양."""
    encoded = base64.b64encode(csv_text.encode("utf-8")).decode("ascii")
    return json.dumps({"content": encoded, "mimeType": "text/csv", "title": "주식 포트폴리오"})


def test_portfolio_parser_reads_drive_csv_download(tmp_path: Path) -> None:
    """read_file_content가 요약만 돌려주게 된 뒤(2026-09-28) 정본 입력은 CSV 내보내기 응답이다."""
    module = load_script("daily-digest", "extract_portfolio")
    dump = tmp_path / "download.json"
    dump.write_text(_drive_download_response(PORTFOLIO_CSV), encoding="utf-8")

    snapshot = module.build_snapshot(module.load_content(dump), "2026-09-28")

    assert "잔고 ₩21,000,000 · 총손익 ₩1,000,000 · 투자금 ₩20,000,000 · 수익률 5.00%" in snapshot
    assert "## 보유 (2종목 + 현금)" in snapshot
    assert "| 삼성 | 오메가기판 | 기판 | 50 |" in snapshot
    # 셀 안의 쉼표(따옴표로 감싼 금액)가 칸을 가르지 않는다
    assert "| 알파전자 | 반도체 | 10 | ₩1,000,000 | ₩1,100,000 | ₩10,000,000 | 10.00% | 52.4% | ₩11,000,000 |" in snapshot
    assert "| _현금 | 현금 |" in snapshot


def test_portfolio_parser_reads_plain_csv(tmp_path: Path) -> None:
    module = load_script("daily-digest", "extract_portfolio")
    dump = tmp_path / "portfolio.csv"
    dump.write_text(PORTFOLIO_CSV, encoding="utf-8")

    snapshot = module.build_snapshot(module.load_content(dump), "2026-09-28")

    assert "## 보유 (2종목 + 현금)" in snapshot
    assert "| 삼성 | 오메가기판 | 기판 | 50 |" in snapshot


@pytest.mark.parametrize("wrapped", [False, True])
def test_portfolio_parser_reads_sheets_range(tmp_path: Path, wrapped: bool) -> None:
    """The connected Sheets range preserves all holdings and formatted amounts."""
    module = load_script("daily-digest", "extract_portfolio")
    values = list(csv.reader(io.StringIO(PORTFOLIO_CSV)))
    values[0][12] = None  # Sheets returns null for empty cells.
    payload = {"range": "'포트폴리오'!A1:AF50", "majorDimension": "ROWS", "values": values}
    if wrapped:
        payload = {"content": [{"type": "text", "text": "Action completed."}],
                   "structuredContent": payload, "isError": False}
    dump = tmp_path / "range.json"
    dump.write_text(json.dumps(payload), encoding="utf-8")
    snapshot = module.build_snapshot(module.load_content(dump), "2026-10-11")
    assert "잔고 ₩21,000,000" in snapshot
    assert "## 보유 (2종목 + 현금)" in snapshot
    assert "| 삼성 | 오메가기판 | 기판 | 50 |" in snapshot


def test_sheets_range_rejects_missing_holding(tmp_path: Path) -> None:
    module = load_script("daily-digest", "extract_portfolio")
    values = list(csv.reader(io.StringIO(PORTFOLIO_CSV)))[:-1]
    dump = tmp_path / "range.json"
    dump.write_text(json.dumps({"majorDimension": "ROWS", "values": values}), encoding="utf-8")
    with pytest.raises(ValueError, match="잔고"):
        module.build_snapshot(module.load_content(dump), "2026-10-11")


def test_portfolio_parser_rejects_truncated_holdings(tmp_path: Path) -> None:
    """행이 잘린 입력은 스냅샷이 되지 않는다 — 2026-09-28 CSV 옮겨 적기에서 마지막 행이 빠져
    이수페타시스가 「전량 청산」으로 읽힐 뻔했다. 보유 행 합계를 시트의 잔고와 대조해 잡는다."""
    module = load_script("daily-digest", "extract_portfolio")
    truncated = PORTFOLIO_CSV.rsplit("\r\n", 1)[0]  # 오메가기판 행 누락
    dump = tmp_path / "download.json"
    dump.write_text(_drive_download_response(truncated), encoding="utf-8")

    with pytest.raises(ValueError, match="잔고"):
        module.build_snapshot(module.load_content(dump), "2026-09-28")


STOP_TIER_DUMP = """|  |  |  |  |  |  |  |  |  |  |  |  |
| :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| 구분 |  계좌 | 섹터 | 종목 | 보유 | 평단 | 현재가 | 매수금액 | 평가금액 | 수익률 | 손익 | 비중 |
| B | 삼성 | 반도체 | 앱실론 | 10 | ₩10,000 | ₩7,800 | ₩100,000 | ₩78,000 | \\-22.00% | \\-₩22,000 | 12.0% | |
| B | 삼성 | 바이오 | 제타 | 10 | ₩10,000 | ₩8,700 | ₩100,000 | ₩87,000 | \\-13.00% | \\-₩13,000 | 11.0% | |
| B | 삼성 | 소비재 | 에타 | 10 | ₩10,000 | ₩8,900 | ₩100,000 | ₩89,000 | \\-11.00% | \\-₩11,000 | 10.0% | |
| B | 삼성 | 화학 | 세타 | 10 | ₩10,000 | ₩9,500 | ₩100,000 | ₩95,000 | \\-5.00% | \\-₩5,000 | 9.0% | |

|  |  |
| :-: | :-: |
| 종목 | 최초 투자 |
"""


def test_portfolio_grades_stop_loss_in_two_tiers() -> None:
    """「매매규칙 6」은 -12% 1차 분할 + -15% 전량 두 티어다(2026-10-21 개정). 깊은 티어가 얕은 티어를 흡수한다."""
    module = load_script("daily-digest", "extract_portfolio")
    snapshot = module.build_snapshot(STOP_TIER_DUMP, "2026-09-05")

    assert "매매규칙 6(-15% 전량 매도): 앱실론 -22.00% (비중 12.0%) ← **위반**" in snapshot
    assert "매매규칙 6(-12% 1차 분할 매도): 제타 -13.00% (비중 11.0%) ← **위반**" in snapshot
    # -22%는 전량 티어에만 잡힌다 — 한 종목이 두 줄에 겹쳐 세어지지 않는다.
    assert "매매규칙 6(-12% 1차 분할 매도): 앱실론" not in snapshot
    # -11%는 축소 고려(-8%), -5%는 아무 데도 안 걸린다.
    assert "매매규칙 7(-8% 비중 축소 고려): 에타 -11.00% (비중 10.0%)" in snapshot
    assert "세타" not in snapshot.split("## 룰 자동 판정")[1]


WEIGHT_DUMP = """|  |  |  |  |  |  |  |  |  |  |  |  |
| :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| 구분 |  계좌 | 섹터 | 종목 | 보유 | 평단 | 현재가 | 매수금액 | 평가금액 | 수익률 | 손익 | 비중 |
| B | 삼성 | 반도체 | 이오타 | 10 | ₩10,000 | ₩12,000 | ₩100,000 | ₩120,000 | 20.00% | ₩20,000 | 36.0% | |
| B | 삼성 | 바이오 | 카파 | 10 | ₩10,000 | ₩11,000 | ₩100,000 | ₩110,000 | 10.00% | ₩10,000 | 31.0% | |

|  |  |
| :-: | :-: |
| 종목 | 최초 투자 |
"""


def test_portfolio_weight_cap_is_the_valuation_axis() -> None:
    """「매매규칙 9」의 35%는 **평가 비중** 상한이다. 매수원금 비중 상한(2%÷손절률)과 다른 축이라 라벨로 갈라 쓴다."""
    module = load_script("daily-digest", "extract_portfolio")
    snapshot = module.build_snapshot(WEIGHT_DUMP, "2026-09-05")

    assert "매매규칙 9(평가 비중 35% 상한): 이오타 36.0% ← **위반**" in snapshot
    # 시트의 비중은 평가 비중이므로 매수원금 축 룰을 여기에 인용하지 않는다.
    assert "매수원금" not in snapshot.split("## 룰 자동 판정")[1]


INDEX_ETF_DUMP = """|  |  |  |  |  |  |  |  |  |  |  |  |
| :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| 구분 |  계좌 | 섹터 | 종목 | 보유 | 평단 | 현재가 | 매수금액 | 평가금액 | 수익률 | 손익 | 비중 |
| B | 삼성 | 지수 | KODEX 200 | 10 | ₩10,000 | ₩8,700 | ₩100,000 | ₩87,000 | \\-13.00% | \\-₩13,000 | 16.0% | |
| B | 삼성 | 지수 | KODEX 코스닥150 | 10 | ₩10,000 | ₩13,000 | ₩100,000 | ₩130,000 | 30.00% | ₩30,000 | 14.0% | |
| B | 삼성 | 반도체 | 제타 | 10 | ₩10,000 | ₩8,700 | ₩100,000 | ₩87,000 | \\-13.00% | \\-₩13,000 | 11.0% | |

|  |  |
| :-: | :-: |
| 종목 | 최초 투자 |
"""


def test_index_etf_skips_average_cost_stop_and_cushion() -> None:
    """「지수 ETF 규칙 4」: 「매매규칙 6」 평단 손절은 지수 ETF에 적용하지 않는다. 매도는 추세 꺾임이다."""
    module = load_script("daily-digest", "extract_portfolio")
    findings = module.build_snapshot(INDEX_ETF_DUMP, "2026-10-28").split("## 룰 자동 판정")[1]

    assert "매매규칙 6(-12% 1차 분할 매도): 제타 -13.00% (비중 11.0%) ← **위반**" in findings
    lines = [line for line in findings.splitlines() if line.startswith("- 매매규칙")]
    assert not any("KODEX" in line for line in lines if "종목 수" not in line)


def test_index_etf_is_not_graded_by_the_35_percent_cap() -> None:
    """「지수 ETF 규칙 3」이 비중 계산을 대신하므로 지수 ETF는 「매매규칙 9」 35% 줄에 오르지 않는다."""
    module = load_script("daily-digest", "extract_portfolio")
    dump = INDEX_ETF_DUMP.replace("| 16.0% |", "| 36.0% |")
    findings = module.build_snapshot(dump, "2026-10-28").split("## 룰 자동 판정")[1]

    assert "매매규칙 9(평가 비중 35% 상한): 해당 없음" in findings


def etf_cap_dump(
    kodex200: tuple[str, str, str], kosdaq150: tuple[str, str, str], cost_column: bool = True
) -> str:
    """ETF 두 행의 (매수금액, 평가금액, 비중)을 받아 합계 ₩1,000,000짜리 시트를 만든다.

    평가금액 합계(현금 포함)가 정확히 ₩1,000,000이 되도록 현금 행이 나머지를 채운다.
    """
    def won(text: str) -> int:
        return int(text.replace("₩", "").replace(",", ""))

    stock_value = 270_000
    cash = 1_000_000 - won(kodex200[1]) - won(kosdaq150[1]) - stock_value
    rows = [
        ("지수", "KODEX 200", *kodex200),
        ("지수", "KODEX 코스닥150", *kosdaq150),
        ("반도체", "제타", "₩250,000", f"₩{stock_value:,}", "27.0%"),
        ("현금", "\\_현금", f"₩{cash:,}", f"₩{cash:,}", f"{cash / 10_000:.1f}%"),
    ]
    header = "| 구분 | 계좌 | 섹터 | 종목 | 보유 | 평단 | 현재가 | 매수금액 | 평가금액 | 수익률 | 손익 | 비중 |"
    lines = ["|  " * 12 + "|", "| :-: " * 12 + "|", header]
    for sector, name, cost, value, weight in rows:
        lines.append(
            f"| B | 삼성 | {sector} | {name} | 1 | ₩1 | ₩1 | {cost} | {value} | 0.00% | ₩0 | {weight} |"
        )
    text = "\n".join(lines) + "\n"
    if not cost_column:
        text = "\n".join(
            "|".join(cell for i, cell in enumerate(line.split("|")) if i != 8)
            for line in text.splitlines()
        ) + "\n"
    return text


def etf_cap_line(dump: str) -> str:
    module = load_script("daily-digest", "extract_portfolio")
    findings = module.build_snapshot(dump, "2026-10-28").split("## 룰 자동 판정")[1]
    return next(line for line in findings.splitlines() if line.startswith("- 지수 ETF 규칙 3"))


ETF_CAP_LABEL = "- 지수 ETF 규칙 3(ETF별 매수원금 15% 상한 · 상승분 허용): "


def test_index_etf_buy_driven_excess_is_a_violation() -> None:
    """2026-10-28 사용자 결정: 15% 상한은 매수원금 비중(매수금액 ÷ 총자산)에 건다 — 넘기면 매수가 넘긴 것이다."""
    line = etf_cap_line(etf_cap_dump(("₩160,000", "₩150,000", "15.0%"), ("₩50,000", "₩60,000", "6.0%")))

    assert line == ETF_CAP_LABEL + "KODEX 200 15.0% ← **위반**(매수원금 16.0%)"


def test_index_etf_rise_only_excess_is_allowed() -> None:
    """매수 없이 주가 상승으로 평가 비중이 15%를 넘은 것은 「매매규칙 9」처럼 허용한다 — 위반이 아니다."""
    line = etf_cap_line(etf_cap_dump(("₩120,000", "₩170,000", "17.0%"), ("₩50,000", "₩60,000", "6.0%")))

    assert line == ETF_CAP_LABEL + "KODEX 200 17.0% ← 상승분 초과 · 허용(매수원금 12.0%)"
    assert "위반" not in line


def test_index_etf_cap_marks_reaching_and_nearing_on_the_cost_axis() -> None:
    """도달·근접(13%↑)은 남은 매수 여유를 보는 것이라 매수원금 비중으로 판정하고, 여유 있는 ETF는 생략한다."""
    line = etf_cap_line(etf_cap_dump(("₩150,000", "₩140,000", "14.0%"), ("₩135,000", "₩120,000", "12.0%")))

    assert line == ETF_CAP_LABEL + (
        "KODEX 200 14.0% ← 상한 도달(매수원금 15.0%), KODEX 코스닥150 12.0% ← 근접(매수원금 13.5%)"
    )


def test_index_etf_line_says_none_when_all_have_room() -> None:
    line = etf_cap_line(etf_cap_dump(("₩50,000", "₩60,000", "6.0%"), ("₩90,000", "₩140,000", "14.0%")))

    assert line == ETF_CAP_LABEL + "해당 없음"


def test_index_etf_excess_without_cost_column_is_not_called_a_violation() -> None:
    """매수금액 열은 선택이다. 없으면 매수로 넘었는지 상승으로 넘었는지 가를 수 없어 위반이라 하지 않는다."""
    line = etf_cap_line(
        etf_cap_dump(("₩160,000", "₩170,000", "17.0%"), ("₩50,000", "₩60,000", "6.0%"), cost_column=False)
    )

    assert line == ETF_CAP_LABEL + "KODEX 200 17.0% ← 초과(매수·상승 구분 불가 — 매수금액 열 없음)"


def test_index_etf_near_cap_without_cost_column_falls_back_to_evaluation_weight() -> None:
    line = etf_cap_line(
        etf_cap_dump(("₩160,000", "₩140,000", "14.0%"), ("₩50,000", "₩60,000", "6.0%"), cost_column=False)
    )

    assert line == ETF_CAP_LABEL + "KODEX 200 14.0% ← 근접(평가 비중 기준 — 매수금액 열 없음)"


def test_index_etf_lines_are_omitted_without_index_etfs() -> None:
    module = load_script("daily-digest", "extract_portfolio")
    findings = module.build_snapshot(WEIGHT_DUMP, "2026-09-05").split("## 룰 자동 판정")[1]

    assert "지수 ETF" not in findings


def test_leveraged_index_etf_keeps_individual_stock_rules() -> None:
    """「지수 ETF 규칙 1」: 레버리지·인버스는 대상이 아니다 — 섹터가 `지수`여도 평단 손절 줄에 남는다."""
    module = load_script("daily-digest", "extract_portfolio")
    dump = INDEX_ETF_DUMP.replace("| KODEX 200 |", "| KODEX 코스닥150레버리지 |")
    findings = module.build_snapshot(dump, "2026-10-28").split("## 룰 자동 판정")[1]

    assert "매매규칙 6(-12% 1차 분할 매도): KODEX 코스닥150레버리지 -13.00%" in findings
    assert "지수 ETF 규칙 4(매도 = 평단 +8% 후 최고 종가 -3%): KODEX 코스닥150 +30.00% " in findings


def test_index_etf_sell_is_pointed_to_rule_4() -> None:
    """평단 손절을 빼는 대신, 매도 판정이 「지수 ETF 규칙 4」(종가 이력 필요)라는 안내를 남긴다."""
    module = load_script("daily-digest", "extract_portfolio")
    findings = module.build_snapshot(INDEX_ETF_DUMP, "2026-10-28").split("## 룰 자동 판정")[1]

    assert (
        "- 지수 ETF 규칙 4(매도 = 평단 +8% 후 최고 종가 -3%): KODEX 200 -13.00%, KODEX 코스닥150 +30.00% "
        "— 평단 손절·익절 쿠션 미적용, 종가 이력으로 별도 판정"
    ) in findings
