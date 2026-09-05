#!/usr/bin/env python3
"""Google Sheets '주식 포트폴리오' → 「포트폴리오」 시트 스냅샷 추출기.

Drive MCP `read_file_content`가 워크북 전 시트를 마크다운 표로 이어붙여 반환하고,
크기 초과로 `{"fileContent": "..."}` JSON 덤프 파일에 저장한다. 이 스크립트는 그 덤프를
파싱해 첫 시트(보유 종목 + 섹터 집계)만 뽑고, `context/my_rules.md` 임계값 기준으로
룰 위반·근접 항목을 계산한다.

네트워크 접근 없음. 의존성: stdlib만 사용.
실패 시 stderr에 에러 출력 후 exit 1 — 호출측(스킬)은 실패해도 브리핑을 계속 진행한다.
"""

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

# 「포트폴리오」 시트 다음 시트(매매기록)의 헤더 표식 — 여기서부터는 버린다
NEXT_SHEET_MARKER = "최초 투자"

# context/my_rules.md 임계값 — 룰 원문이 정본이다.
# `tests/test_agent_configuration.py::test_portfolio_thresholds_match_my_rules`가
# 룰 파일에서 숫자를 파싱해 아래 상수와 대조한다. 룰이 바뀌면 그 테스트가 먼저 깨진다.
STOP_LOSS_PCT = -15.0  # 매매규칙 6: -15% 1차 분할 매도
STOP_FULL_PCT = -20.0  # 매매규칙 6: -20% 전량 매도
TRIM_PCT = -10.0  # 매매규칙 7: -10% 비중 축소 고려
PROFIT_CUSHION_PCT = 24.0  # 매매규칙 11: 24~30% 수익 시 보유량 10~30% 익절
# 매매규칙 9는 두 축을 나눈다. 아래는 **평가 비중** 축(주가 상승분 허용, 최대 35%)이다.
# 매수원금 비중 상한(= 2% ÷ 계획 손절률)은 스냅샷이 아니라 진입 판단에서 계산한다.
MAX_WEIGHT_PCT = 35.0
WEIGHT_WARN_PCT = 30.0  # 상한 근접 경고선
MIN_HOLDINGS = 5  # 매매규칙 9: 5~12종목
MAX_HOLDINGS = 12

CASH_SECTOR = "현금"


def unescape(text: str) -> str:
    """마크다운 이스케이프(\\-, \\_, \\~, \\!) 해제."""
    return re.sub(r"\\([-_~!*#|])", r"\1", text).strip()


def split_row(line: str) -> list[str]:
    """`| a | b |` 형태의 행을 셀 리스트로."""
    return [unescape(c) for c in line.strip().strip("|").split("|")]


def split_blocks(content: str) -> list[list[str]]:
    """빈 줄 기준으로 표 블록 분할. 구분선(`| :-: |`) 행은 제거한다."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in content.split("\n"):
        if not line.strip():
            if current:
                blocks.append(current)
                current = []
            continue
        if ":-:" in line or set(line.replace("|", "").replace(" ", "")) <= {"-", ":"}:
            continue
        current.append(line)
    if current:
        blocks.append(current)
    return blocks


def portfolio_blocks(content: str) -> list[list[str]]:
    """첫 시트(포트폴리오)에 해당하는 블록만 반환."""
    out: list[list[str]] = []
    for block in split_blocks(content):
        if any(NEXT_SHEET_MARKER in line for line in block):
            break
        out.append(block)
    return out


def to_float(text: str) -> float | None:
    """'₩1,466,000', '-2.85%', '1,600' → float. 파싱 불가면 None."""
    cleaned = re.sub(r"[₩,%\s]", "", text)
    if not cleaned or cleaned in {"-", "#DIV/0!"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_totals(header_cells: list[str]) -> dict[str, str]:
    """헤더 행 오른쪽에 붙어 있는 `잔고|₩…|총손익|₩…` 라벨-값 쌍을 추출."""
    labels = ("잔고", "총손익", "투자금", "수익률")
    value_re = re.compile(r"^-?₩?[\d,.]+%?$")
    totals: dict[str, str] = {}
    for i, cell in enumerate(header_cells):
        # 같은 이름의 컬럼 헤더(예: '수익률')와 섞이지 않도록 값 형태까지 확인한다
        if cell in labels and i + 1 < len(header_cells) and value_re.match(header_cells[i + 1]):
            totals[cell] = header_cells[i + 1]
    return {label: totals[label] for label in labels if label in totals}


def parse_holdings(block: list[str]) -> tuple[list[dict], dict[str, str]]:
    """보유 종목 블록 → (종목 dict 리스트, 총계 dict)."""
    header = split_row(block[0])
    totals = parse_totals(header)
    try:
        base = header.index("종목")
    except ValueError:
        raise ValueError("보유 종목 표에서 '종목' 컬럼을 찾지 못했다")

    fields = ["종목", "보유", "평단", "현재가", "매수금액", "평가금액", "수익률", "손익", "비중", "메모"]
    holdings: list[dict] = []
    for line in block[1:]:
        cells = split_row(line)
        if len(cells) <= base or not cells[base]:
            continue
        row = {name: (cells[base + i] if base + i < len(cells) else "") for i, name in enumerate(fields)}
        row["섹터"] = cells[base - 1] if base >= 1 else ""
        row["계좌"] = cells[base - 2] if base >= 2 else ""
        row["수익률_v"] = to_float(row["수익률"])
        row["비중_v"] = to_float(row["비중"])
        holdings.append(row)
    return holdings, totals


def parse_sectors(block: list[str]) -> list[dict]:
    """섹터 집계 블록 → 섹터 dict 리스트 (빈 행·#DIV/0! 제거)."""
    header = split_row(block[0])
    # 같은 블록에 섹터 표가 두 벌(비중순/수익률순) 있다. 수익률 컬럼이 있는 쪽을 쓴다.
    starts = [i for i, c in enumerate(header) if c == "섹터"]
    if not starts:
        return []
    start = starts[-1]
    cols = header[start:]
    sectors: list[dict] = []
    for line in block[1:]:
        cells = split_row(line)[start:]
        if not cells or not cells[0]:
            continue
        row = {cols[i]: cells[i] for i in range(min(len(cols), len(cells)))}
        if "#DIV/0!" in "".join(row.values()):
            continue
        sectors.append(row)
    return sectors


def rule_findings(holdings: list[dict]) -> list[str]:
    """보유 현황을 my_rules.md 임계값과 대조해 판정 문구 생성."""
    positions = [h for h in holdings if h["섹터"] != CASH_SECTOR]
    findings: list[str] = []

    def names(pred) -> list[str]:
        return [
            f"{h['종목']} {h['수익률_v']:+.2f}% (비중 {h['비중']})"
            for h in positions
            if h["수익률_v"] is not None and pred(h["수익률_v"])
        ]

    # 「매매규칙 6」은 두 티어다. 깊은 티어가 얕은 티어를 흡수해 한 종목이 두 줄에 겹치지 않는다.
    full_exit = names(lambda v: v <= STOP_FULL_PCT)
    findings.append(
        f"- 매매규칙 6(-20% 전량 매도): {', '.join(full_exit)} ← **위반**" if full_exit
        else "- 매매규칙 6(-20% 전량 매도): 해당 없음"
    )

    tier1 = names(lambda v: STOP_LOSS_PCT >= v > STOP_FULL_PCT)
    findings.append(
        f"- 매매규칙 6(-15% 1차 분할 매도): {', '.join(tier1)} ← **위반**" if tier1
        else "- 매매규칙 6(-15% 1차 분할 매도): 해당 없음"
    )

    trim = names(lambda v: TRIM_PCT >= v > STOP_LOSS_PCT)
    findings.append(
        f"- 매매규칙 7(-10% 비중 축소 고려): {', '.join(trim)}" if trim
        else "- 매매규칙 7(-10% 비중 축소 고려): 해당 없음"
    )

    cushion = names(lambda v: v >= PROFIT_CUSHION_PCT)
    findings.append(
        f"- 매매규칙 11(24~30%↑ 익절 쿠션): {', '.join(cushion)}" if cushion
        else "- 매매규칙 11(24~30%↑ 익절 쿠션): 해당 없음"
    )

    over = [h for h in positions if h["비중_v"] is not None and h["비중_v"] >= MAX_WEIGHT_PCT]
    near = [
        h for h in positions
        if h["비중_v"] is not None and MAX_WEIGHT_PCT > h["비중_v"] >= WEIGHT_WARN_PCT
    ]
    def weight_desc(rows: list[dict]) -> str:
        return ", ".join(f"{h['종목']} {h['비중']}" for h in rows)

    if over:
        findings.append(f"- 매매규칙 9(평가 비중 35% 상한): {weight_desc(over)} ← **위반**")
    elif near:
        findings.append(f"- 매매규칙 9(평가 비중 35% 상한): {weight_desc(near)} ← 근접")
    else:
        findings.append("- 매매규칙 9(평가 비중 35% 상한): 해당 없음")

    count = len(positions)
    if count > MAX_HOLDINGS:
        state = "← **초과**"
    elif count == MAX_HOLDINGS or count == MIN_HOLDINGS:
        state = "← 경계"
    elif count < MIN_HOLDINGS:
        state = "← **미달**"
    else:
        state = ""
    findings.append(f"- 매매규칙 9(종목 수 5~12): {count}종목 {state}".rstrip())

    cash = [h for h in holdings if h["섹터"] == CASH_SECTOR]
    if cash:
        findings.append(f"- 현금 비중: {cash[0]['비중']} ({cash[0]['평가금액']})")
    return findings


def render(holdings: list[dict], sectors: list[dict], totals: dict[str, str], today: str) -> str:
    positions = [h for h in holdings if h["섹터"] != CASH_SECTOR]
    has_cash = len(positions) != len(holdings)
    total_line = " · ".join(f"{k} {v}" for k, v in totals.items()) or "(총계 없음)"

    lines = [
        f"# 포트폴리오 스냅샷 — {today}",
        "",
        f"> 출처: Google Sheets '주식 포트폴리오' / 「포트폴리오」 시트 · 수집 {dt.datetime.now().isoformat(timespec='seconds')}",
        f"> {total_line}",
        "",
        f"## 보유 ({len(positions)}종목{' + 현금' if has_cash else ''})",
        "",
        "| 종목 | 섹터 | 보유 | 평단 | 현재가 | 수익률 | 비중 | 평가금액 | 투자 아이디어 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for h in holdings:
        lines.append(
            f"| {h['종목']} | {h['섹터']} | {h['보유']} | {h['평단']} | {h['현재가']} | "
            f"{h['수익률']} | {h['비중']} | {h['평가금액']} | {h['메모']} |"
        )

    if sectors:
        cols = list(sectors[0].keys())
        lines += [
            "",
            "## 섹터 비중",
            "",
            "| " + " | ".join(cols) + " |",
            "| " + " | ".join("---" for _ in cols) + " |",
        ]
        for s in sectors:
            lines.append("| " + " | ".join(s.get(c, "") for c in cols) + " |")

    lines += ["", "## 룰 자동 판정", "", *rule_findings(holdings), ""]
    return "\n".join(lines)


def build_snapshot(content: str, today: str) -> str:
    """덤프 본문 → 스냅샷 마크다운."""
    blocks = portfolio_blocks(content)
    if not blocks:
        raise ValueError("포트폴리오 시트 블록을 찾지 못했다 — 시트 순서/구조 변경 가능성")
    holdings, totals = parse_holdings(blocks[0])
    if not holdings:
        raise ValueError("보유 종목 행을 하나도 파싱하지 못했다")
    sectors = parse_sectors(blocks[1]) if len(blocks) > 1 else []
    return render(holdings, sectors, totals, today)


def load_content(path: Path) -> str:
    """MCP 덤프(JSON) 또는 원문 마크다운 모두 허용."""
    raw = path.read_text(encoding="utf-8")
    try:
        return json.loads(raw)["fileContent"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return raw


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump", type=Path, help="Drive MCP read_file_content가 저장한 덤프 경로")
    parser.add_argument("--out", type=Path, help="스냅샷 저장 경로 (예: output/portfolio/2026-08-09.md)")
    parser.add_argument("--date", default=dt.date.today().isoformat(), help="스냅샷 날짜 (기본: 오늘)")
    args = parser.parse_args()

    try:
        snapshot = build_snapshot(load_content(args.dump), args.date)
    except (OSError, ValueError) as e:
        print(f"ERROR: 포트폴리오 파싱 실패 — {e}", file=sys.stderr)
        return 1

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(snapshot, encoding="utf-8")
        print(f"저장: {args.out}", file=sys.stderr)
    print(snapshot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
