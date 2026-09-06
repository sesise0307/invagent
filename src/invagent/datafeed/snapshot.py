"""포트폴리오 스냅샷 인제스트.

`output/portfolio/<날짜>.md`는 Google Sheets 덤프에서 만들어져 여러 스크립트가 다시 읽는다.
쓰는 쪽(`extract_portfolio`)과 읽는 쪽(`peak_drawdown`·`portfolio_diff`)이 각자 표 파서를
들고 있었고, 셀에서 마크다운 이스케이프를 푸느냐 마느냐가 서로 달랐다. 여기서 하나로 두고
그 차이는 인자로 고른다.

네트워크 접근 없음.
"""

from __future__ import annotations

import re

CASH_SECTOR = "현금"
HOLDINGS_HEADING = "## 보유"
# 스냅샷 세대에 따라 종목 열 이름이 다르다.
NAME_KEYS = ("종목", "종목명")

# 시트가 값 대신 내보내는 것들. 0이 아니라 "값 없음"이다.
SHEET_SENTINELS = {"-", "#DIV/0!", "#N/A", "#VALUE!"}

_ESCAPE_RE = re.compile(r"\\([-_~!*#|])")
_NUMBER_NOISE_RE = re.compile(r"[₩,%\s]")
_BALANCE_RE = re.compile(r"잔고\s*₩([\d,]+)")


def _unescape(text: str) -> str:
    """마크다운 이스케이프(\\-, \\_, \\~, \\!) 해제."""
    return _ESCAPE_RE.sub(r"\1", text).strip()


unescape = _unescape


def split_row(line: str, *, unescape: bool = False) -> list[str]:
    """`| a | b |` 형태의 행을 셀 리스트로.

    `unescape=True`는 시트 원본에서 표를 만들 때 쓴다 — 이미 만들어진 스냅샷을 다시 읽을
    때는 셀이 그대로 비교 대상이므로 손대지 않는다.
    """
    cells = line.strip().strip("|").split("|")
    return [_unescape(c) if unescape else c.strip() for c in cells]


def to_float(text: str | None) -> float | None:
    """'₩1,466,000', '-2.85%', '1,600' → float. 값이 없으면 None."""
    cleaned = _NUMBER_NOISE_RE.sub("", text or "")
    if not cleaned or cleaned in SHEET_SENTINELS:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_holdings(text: str, *, require_rows: bool = True) -> list[dict]:
    """스냅샷의 `## 보유` 표 → 종목 dict 리스트 (현금 행 제외).

    `require_rows=False`는 스냅샷 두 개를 비교할 때 쓴다 — 보유가 0건인 날도 정상 입력이다.
    """
    lines = text.split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith(HOLDINGS_HEADING))
    except StopIteration:
        raise ValueError(f"스냅샷에서 '{HOLDINGS_HEADING}' 섹션을 찾지 못했다")

    header: list[str] | None = None
    rows: list[dict] = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        if not line.strip().startswith("|"):
            continue
        cells = split_row(line)
        if set("".join(cells)) <= {"-", ":"}:
            continue
        if header is None:
            header = cells
            continue
        row = {header[i]: cells[i] for i in range(min(len(header), len(cells)))}
        if not any(row.get(k) for k in NAME_KEYS) or row.get("섹터") == CASH_SECTOR:
            continue
        rows.append(row)

    if header is None:
        raise ValueError("보유 표 헤더를 찾지 못했다")
    if require_rows and not rows:
        raise ValueError("보유 종목 행을 하나도 파싱하지 못했다")
    return rows


def parse_balance(text: str) -> float | None:
    """스냅샷 머리말의 `잔고 ₩…` → float."""
    m = _BALANCE_RE.search(text)
    return float(m.group(1).replace(",", "")) if m else None
