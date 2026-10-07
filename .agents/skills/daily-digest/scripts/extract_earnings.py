"""raw export의 실적 공시를 「실적정리」 시트에 쓸 셀 값으로 바꾼다.

daily-digest 1-6단계에서 쓴다. 공시 봇(AWAKE PRO 실적발표 채널) 메시지를 포워드한
블록만 읽는다 — 회사 줄, `📁 공시명`, 공시 시각 순서로 오는 본문이다.

    uv run python .agents/skills/daily-digest/scripts/extract_earnings.py \\
        output/daily-digest/raw/<today>_raw.md

의존성은 표준 라이브러리와 `invagent` 패키지뿐이다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from invagent.datafeed import stockeasy

MESSAGE_HEADER_RE = re.compile(r"^\*\*\[[^\]]*\]\*\* \d{4}-\d{2}-\d{2}", re.M)
TITLE_RE = re.compile(r"^📁 (.+)$", re.M)
COMPANY_RE = re.compile(r"^\S+ (.+?)\(시가총액")
DISCLOSED_RE = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2}) \d{2}:\d{2}$", re.M)
RCP_RE = re.compile(r"rcpNo=(\d+)")
CODE_RE = re.compile(r"code=(\d{6})")
AMOUNT = r"(-?[\d,]+)억"
LINE_RE = {
    "revenue": re.compile(r"^(?:\(연간\))?매출액 : " + AMOUNT + r"(.*)$", re.M),
    "op": re.compile(r"^영업익 : " + AMOUNT + r"(.*)$", re.M),
}
CONSENSUS_RE = re.compile(r"예상치 : " + AMOUNT)

CONFIRMED_TITLE_RE = re.compile(r"^(?:분기|반기|사업)보고서 \((\d{4})\.(\d{2})\)$")
# 「최근 실적」 표의 한 줄. 라벨(2026.2Q)은 봇이 한 칸 밀려 찍을 때가 있어 읽지 않고
# 순서만 쓴다 — 첫 줄이 이번 분기, 둘째 줄이 직전 분기, 다섯째 줄이 전년 동기다.
# 빈 값은 `-`로 찍힌다 — 그 줄도 받아야 순서가 밀리지 않는다.
QUARTER_ROW_RE = re.compile(r"^\d{4}\.\dQ\s*:?\s*(-?[\d,]+억|-)/\s*(-?[\d,]+억|-)/", re.M)
# 봇이 붙이는 실적 태그. 어닝 서프라이즈·쇼크는 서프율 칸과 겹쳐 받지 않는다.
TAG_RE = re.compile(r"^- (최근 \d+개.+?)\s*$", re.M)
CHANGE_TITLE_RE = re.compile(r"^매출액또는손익구조30%.*이상(?:변동|변경)$")
PRELIMINARY_TITLES = {
    "연결재무제표기준영업(잠정)실적(공정공시)": "연결",
    "영업(잠정)실적(공정공시)": "개별",
}


def _amount(text: str) -> int:
    return int(text.replace(",", ""))


def _cell_amount(text: str) -> int | None:
    return None if text == "-" else _amount(text.removesuffix("억"))


def _line_values(body: str, key: str) -> dict | None:
    match = LINE_RE[key].search(body)
    if not match:
        return None
    consensus = CONSENSUS_RE.search(match.group(2))
    return {
        "actual": _amount(match.group(1)),
        "consensus": _amount(consensus.group(1)) if consensus else None,
    }


def _preliminary_tab(year: int, month: int) -> str:
    """잠정 공시는 공시일이 속한 분기의 직전 분기 실적이다 (10월 공시 → 3분기)."""
    quarter = (month - 1) // 3
    if quarter == 0:
        return f"4q{(year - 1) % 100:02d}"
    return f"{quarter}q{year % 100:02d}"


def _confirmed_tab(year: int, month: int) -> tuple[str | None, str | None]:
    """확정 보고서는 제목의 보고 기간으로 탭을 정한다. 분기 말 결산이 아니면 쓰지 않는다."""
    if month % 3:
        return None, f"보고 기간 {year:04d}.{month:02d} — 분기 말 결산이 아니라 탭을 정할 수 없음"
    return f"{month // 3}q{year % 100:02d}", None


def _split_messages(raw_text: str) -> list[str]:
    starts = [m.start() for m in MESSAGE_HEADER_RE.finditer(raw_text)] or [0]
    starts.append(len(raw_text))
    return [raw_text[a:b] for a, b in zip(starts, starts[1:])]


def parse_earnings(raw_text: str) -> list[dict]:
    """raw 전문에서 실적 공시 블록을 찾아 항목 리스트로 돌려준다."""
    entries: list[dict] = []
    for message in _split_messages(raw_text):
        title_match = TITLE_RE.search(message)
        if not title_match:
            continue
        title = title_match.group(1).strip()
        confirmed = CONFIRMED_TITLE_RE.match(title)
        change = CHANGE_TITLE_RE.match(title)
        if title not in PRELIMINARY_TITLES and not confirmed and not change:
            continue
        before = message[: title_match.start()].rstrip("\n").split("\n")
        company = COMPANY_RE.match(before[-1]) if before else None
        disclosed = DISCLOSED_RE.search(message, title_match.end())
        if not company or not disclosed:
            continue
        year, month, day = (int(g) for g in disclosed.groups())
        rcp = RCP_RE.search(message)
        code = CODE_RE.search(message)
        annual = False
        if change:
            kind = "변동"
            basis = "개별" if re.search(r"^재무제표 종류 : (?:별도|개별)", message, re.M) else "연결"
            # 연간 값만 실린 형식은 탭과 분기 값을 derive_quarter가 정한다.
            annual = "(연간)" in message
            tab, skip_reason = (None, None) if annual else (_preliminary_tab(year, month), None)
        elif confirmed:
            kind = "확정"
            basis = "개별" if "구분 : 개별실적" in message else "연결"
            tab, skip_reason = _confirmed_tab(int(confirmed.group(1)), int(confirmed.group(2)))
        else:
            kind, basis = "잠정", PRELIMINARY_TITLES[title]
            tab, skip_reason = _preliminary_tab(year, month), None
        entries.append({
            "name": company.group(1).strip(),
            "code": code.group(1) if code else None,
            "kind": kind,
            "basis": basis,
            "disclosed": f"{year:04d}-{month:02d}-{day:02d}",
            "tab": tab,
            "skip_reason": skip_reason,
            "annual": annual,
            "revenue": _line_values(message, "revenue"),
            "op": _line_values(message, "op"),
            "quarters": [
                {"revenue": _cell_amount(r), "op": _cell_amount(o)}
                for r, o in QUARTER_ROW_RE.findall(message)
            ],
            "tags": TAG_RE.findall(message),
            "rcp_no": rcp.group(1) if rcp else None,
        })
    # 같은 분기를 연결·개별로 둘 다 냈으면 연결만 쓴다 — 시트의 서프율은 연결 컨센 기준이다.
    consolidated = {(e["name"], e["tab"], e["kind"]) for e in entries if e["basis"] == "연결"}
    return [
        e for e in entries
        if e["basis"] == "연결" or (e["name"], e["tab"], e["kind"]) not in consolidated
    ]


def _percent(value: float) -> str:
    return f"{value:.1f}%"


def _surprise(values: dict | None) -> str:
    """(실제 - 예상) / |예상|. 예상이 음수여도 개선이면 양수가 되도록 절댓값으로 나눈다."""
    if not values or not values.get("consensus"):
        return ""
    consensus = values["consensus"]
    return _percent((values["actual"] - consensus) / abs(consensus) * 100)


# 표에서 직접 재는 5분기 최대·최소와 겹치는 봇 태그.
DUPLICATE_TAG_RE = re.compile(r"^최근 \d+개분기 (?:최대|최소) 영업익$")
EXTREMES_WINDOW = 5


def _change(current: int, base: int) -> str:
    """증감률. 기준이 손실이거나 부호가 바뀌면 %가 뜻이 없어 말로 적는다."""
    if base > 0:
        if current < 0:
            return "적자 전환"
        return f"{(current - base) / base * 100:+.1f}%"
    if current > 0:
        return "흑자 전환"
    if base == 0:
        return "적자 전환" if current < 0 else "변동 없음"
    if current == base:
        return "적자 지속"
    return "적자 축소" if current > base else "적자 확대"


def _extremes_notes(quarters: list[dict]) -> list[str]:
    if len(quarters) < EXTREMES_WINDOW:
        return []
    window = quarters[:EXTREMES_WINDOW]
    notes = []
    for word, pick in (("최대", max), ("최소", min)):
        hits = [
            label for label, key in (("매출", "revenue"), ("영익", "op"))
            if all(q[key] is not None for q in window)
            and window[0][key] == pick(q[key] for q in window)
        ]
        if hits:
            notes.append(f"{EXTREMES_WINDOW}분기 내 {word} {'·'.join(hits)}")
    return notes


def _growth_note(label: str, quarters: list[dict], key: str) -> str | None:
    parts = []
    if not quarters or quarters[0][key] is None:
        return None
    for name, index in (("yoy", 4), ("qoq", 1)):
        if len(quarters) > index and quarters[index][key] is not None:
            parts.append(f"{name} {_change(quarters[0][key], quarters[index][key])}")
    return f"{label} {' '.join(parts)}" if parts else None


def sheet_cells(entry: dict) -> dict:
    """항목 → 시트 C(매출 서프율)·D(영익 서프율)·M(특이 사항) 열 값."""
    quarters = entry.get("quarters") or []
    notes = []
    op = entry.get("op") or {}
    if op.get("consensus") is not None and (op["consensus"] <= 0 or op["actual"] < 0):
        notes.append(f"컨센 {op['consensus']}억, 영익 {op['actual']}억")
    notes += _extremes_notes(quarters)
    notes += [
        note for note in (
            _growth_note("매출", quarters, "revenue"),
            _growth_note("영익", quarters, "op"),
        ) if note
    ]
    if entry.get("derived_from"):
        notes.append(entry["derived_from"])
    # 연간형 공시의 봇 태그는 연간 기준인지 분기 기준인지 알 수 없어 옮기지 않는다.
    if not entry.get("annual"):
        notes += [tag for tag in entry.get("tags") or [] if not DUPLICATE_TAG_RE.match(tag)]
    return {
        "revenue_surprise": _surprise(entry.get("revenue")),
        "op_surprise": _surprise(entry.get("op")),
        "note": " ".join([f"[{entry['kind']}]", ", ".join(notes)]).strip(),
    }


def _months_before(year: int, month: int, months: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) - months
    return index // 12, index % 12 + 1


def derive_quarter(entry: dict, financials: dict | None, primary: str) -> dict:
    """연간형 30% 변동 공시 → 마지막 분기 값. 연간에서 같은 회계연도 앞 세 분기를 뺀다.

    결산월은 StockEasy 연간 행의 `month`로 정한다(3월 결산 등). 앞 세 분기 중 하나라도
    없으면 계산하지 않고 `skip_reason`을 채워 돌려준다.
    """
    result = dict(entry)
    actual, _ = stockeasy.fs_rows(financials or {}, primary, yearly=False)
    yearly, _ = stockeasy.fs_rows(financials or {}, primary, yearly=True)
    fiscal_month = next((row.get("month") for row in yearly if row.get("month")), 12)
    disclosed_year, disclosed_month = (int(x) for x in entry["disclosed"].split("-")[:2])
    fiscal_year = disclosed_year if disclosed_month > fiscal_month else disclosed_year - 1
    if fiscal_month % 3:
        result["skip_reason"] = f"결산월 {fiscal_month}월 — 분기 말 결산이 아니라 탭을 정할 수 없음"
        return result

    by_period = {(row.get("year"), row.get("month")): row for row in actual}
    earlier = []
    for offset in (3, 6, 9, 12):
        row = by_period.get(_months_before(fiscal_year, fiscal_month, offset))
        if row is None or row.get("revenue") is None or row.get("operating_income") is None:
            break
        earlier.append({
            "revenue": round(row["revenue"] / 1e8),
            "op": round(row["operating_income"] / 1e8),
        })
    if len(earlier) < 3:
        missing = _months_before(fiscal_year, fiscal_month, 3 * (len(earlier) + 1))
        result["skip_reason"] = (
            f"{fiscal_year}년 회계연도 {missing[0]}.{missing[1]:02d} 분기 실적이 StockEasy에 없어 "
            "마지막 분기를 계산할 수 없음"
        )
        return result

    annual_revenue, annual_op = entry["revenue"]["actual"], entry["op"]["actual"]
    current = {
        "revenue": annual_revenue - sum(q["revenue"] for q in earlier[:3]),
        "op": annual_op - sum(q["op"] for q in earlier[:3]),
    }
    result.update({
        "tab": f"{fiscal_month // 3}q{fiscal_year % 100:02d}",
        "revenue": {"actual": current["revenue"], "consensus": None},
        "op": {"actual": current["op"], "consensus": None},
        "quarters": [current] + earlier,
        "derived_from": f"연간 매출 {annual_revenue}억·영익 {annual_op}억에서 1~3분기 합을 빼 계산",
    })
    return result


def _fetch_financials(code: str) -> tuple[dict | None, str, str | None]:
    """StockEasy `info-tab`에서 (financials, primary_fs_type, 실패 사유)."""
    info, err = stockeasy.fetch_stock_json(
        stockeasy.ENDPOINTS["info_tab"].format(code=code),
        referer=f"{stockeasy.PAGE_BASE}/{code}",
        cookie=stockeasy.load_cookie(),
    )
    if err or not info:
        return None, "C", f"StockEasy info-tab 실패 — {err}"
    return info.get("financials"), info.get("primary_fs_type") or "C", None


def build_rows(raw_text: str) -> dict:
    """raw 전문 → 시트에 쓸 행과 건너뛴 항목. 연간형 변동 공시만 네트워크를 쓴다."""
    rows, skipped = [], []
    for entry in parse_earnings(raw_text):
        if entry["annual"]:
            if not entry["code"]:
                entry = {**entry, "skip_reason": "종목코드가 없어 앞선 분기 실적을 받을 수 없음"}
            else:
                financials, primary, err = _fetch_financials(entry["code"])
                entry = (
                    {**entry, "skip_reason": err} if err
                    else derive_quarter(entry, financials, primary)
                )
        if entry["tab"] is None:
            skipped.append({
                "name": entry["name"], "kind": entry["kind"],
                "reason": entry.get("skip_reason") or "탭을 정할 수 없음",
                "rcp_no": entry["rcp_no"],
            })
            continue
        rows.append({
            "name": entry["name"], "tab": entry["tab"], "kind": entry["kind"],
            **sheet_cells(entry),
            "rcp_no": entry["rcp_no"],
        })
    return {"rows": rows, "skipped": skipped}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="raw export의 실적 공시 → 실적정리 시트 셀 값(JSON)")
    parser.add_argument("raw", type=Path, help="output/daily-digest/raw/<날짜>_raw.md")
    args = parser.parse_args(argv)
    try:
        raw_text = args.raw.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"ERROR: raw 파일을 읽을 수 없음 — {exc}", file=sys.stderr)
        return 1
    print(json.dumps(build_rows(raw_text), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
