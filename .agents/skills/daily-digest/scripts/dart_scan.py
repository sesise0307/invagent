#!/usr/bin/env python3
"""보유·관심 종목의 기간 내 DART 공시를 OpenDART 목록에서 찾는다 — `daily-digest` 1-5단계 5).

텔레그램 포워드는 공시를 자주 놓친다. 그래서 직전 브리핑일부터 오늘까지의 전체 공시 목록
(`opendart.fetch_filings`)을 보유(최신 스냅샷)·관심(`context/interested_stocks.md`) 종목의 티커로
거르고(`output/watchlist/돌파선_감시.md` 「감시 대상」 표의 종목도 함께), 오늘 raw·링크 파일에 이미 접수번호가 있는 공시는 「텔레그램 수록」으로 따로 센다. 우선주는
본주 티커로 대조한다 — DART에 공시하는 것은 본주다. 본문은 읽지 않는다 — 그것은 MCP의 몫이다.

의존성: 프로젝트 환경(`invagent.datafeed`)만.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from invagent.core.archive import dated_files
from invagent.datafeed import cache, opendart, tickers
from invagent.datafeed.env import output_dir, repo_root
from invagent.datafeed.snapshot import holding_name, parse_holdings, split_row

WATCHLIST_REL = Path("context") / "interested_stocks.md"
BREAKOUT_REL = Path("watchlist") / "돌파선_감시.md"
BREAKOUT_TABLE = "## 감시 대상"
# 표 셀의 `종목명 (0209D0)` — 이름 검색이 안 되는 신형 코드라 괄호 안 코드를 그대로 쓴다.
NAME_CODE_RE = re.compile(r"^(?P<name>.+?)\s*\((?P<code>[0-9A-Z]{6})\)$")
# 브리핑을 오래 쉬었을 때 거슬러 올라갈 한도. 두 영업일이 11페이지라 2주면 70페이지 안팎이다.
MAX_WINDOW = timedelta(days=14)
# 판단을 움직이는 공시. `quick-brief` 2단계의 특이 공시 기준 — 계약·증자·사채·지분·실적·자사주·합병·분할·
# 소각·해명·거래정지·관리종목·사건·이벤트. 나머지(IR·정기보고서·임원 소유 소량 변동·주총 안내)는 일상 공시로 센다.
NOTABLE_KEYWORDS = (
    "공급계약", "잠정", "손익구조", "증자", "감자", "사채", "전환청구", "자기주식", "최대주주", "대량보유",
    "합병", "분할", "영업양", "타법인주식", "유형자산", "시설투자", "투자판단관련", "거래정지", "관리종목",
    "상장폐지", "불성실", "조회공시", "해명", "소각", "횡령", "배임", "소송", "회생", "부도", "배당", "의무보유", "보호예수",
)
# 접수번호 14자리(접수일 8자리 + 일련번호). `rcpNo=`·`rcept_no`·봇 본문 어디에 있든 숫자만 본다.
RCEPT_NO_RE = re.compile(r"(?<!\d)(20\d{12})(?!\d)")


def kst_today() -> date:
    return datetime.fromtimestamp(cache._now(), cache.KST).date()


def briefing_today() -> date:
    """raw·링크 폴더 이름의 날짜. `fetch-messages`가 로컬 시계로 짓는다 — KST가 아니다."""
    return datetime.fromtimestamp(cache._now()).date()


def latest_snapshot() -> Path | None:
    files = dated_files(output_dir() / "portfolio")
    return files[-1] if files else None


def window_start(archive: Path, asof: date) -> date:
    """직전 브리핑일(오늘 제외, 포함 경계). 브리핑이 없으면 전날, 오래됐으면 `MAX_WINDOW`까지만."""
    earlier = [date.fromisoformat(p.stem) for p in dated_files(archive) if p.stem < asof.isoformat()]
    start = earlier[-1] if earlier else asof - timedelta(days=1)
    return max(start, asof - MAX_WINDOW)


def forwarded_rcept_nos(raw: Path, links: Path) -> set[str]:
    """오늘 raw와 링크 폴더(원문·요약) 어디든 나온 접수번호."""
    files = [raw] if raw.is_file() else []
    if links.is_dir():
        files += [p for p in links.rglob("*") if p.is_file()]
    found: set[str] = set()
    for path in files:
        found.update(RCEPT_NO_RE.findall(path.read_text(encoding="utf-8", errors="ignore")))
    return found


def held_names(path: Path | None) -> list[str]:
    if path is None:
        return []
    rows = parse_holdings(path.read_text(encoding="utf-8"), require_rows=False)
    return [holding_name(row) for row in rows]


def load_watchlist(path: Path) -> list[str]:
    """`- 종목명` 줄만 읽는다."""
    if not path.is_file():
        return []
    return [
        line[2:].strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("- ") and line[2:].strip()
    ]


def load_breakout(path: Path) -> list[str]:
    """`돌파선_감시.md` 「감시 대상」 표의 첫 열. 섹터 그룹 표 등 다른 절의 표는 읽지 않는다."""
    if not path.is_file():
        return []
    names: list[str] = []
    in_table = header_seen = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            in_table = line.strip() == BREAKOUT_TABLE
            continue
        if not in_table or not line.strip().startswith("|"):
            continue
        cell = split_row(line)[0].replace("*", "").strip()
        if not header_seen:
            header_seen = True
            continue
        if cell and set(cell) - {"-", ":"}:
            names.append(cell)
    return names


def universe(groups: list[tuple[str, list[str]]]) -> tuple[dict[str, dict], list[str]]:
    """본주 티커 → `{name, held, source}`, 그리고 티커를 못 찾은 종목의 사유 줄.

    `groups`는 `(출처, 종목명들)`을 우선순위 순으로 — 같은 회사가 여럿에 있으면 앞 출처로 남는다.
    """
    codes: dict[str, dict] = {}
    gaps: list[str] = []
    for source, names in groups:
        for name in names:
            pinned = NAME_CODE_RE.match(name)
            if pinned:
                name, code = pinned["name"], pinned["code"]
            else:
                hit, err, _ = tickers.resolve_stock(name, stockeasy_fallback=False)
                if not hit:
                    gaps.append(f"{name}: 티커 {err}")
                    continue
                code = hit["stock_code"]
            code = tickers.common_code(code) or code
            codes.setdefault(code, {"name": name, "held": source == "보유", "source": source})
    return codes, gaps


def _day(rcept_no: str) -> str:
    return f"{rcept_no[:4]}-{rcept_no[4:6]}-{rcept_no[6:8]}"


def render(result: dict) -> str:
    """텔레그램이 놓친 주요 공시가 맨 위다 — 1-5단계 5)가 MCP로 본문을 받을 목록이다."""
    filings = result["filings"]
    missed = [f for f in filings if f["notable"] and not f["in_telegram"]]
    forwarded = [f for f in filings if f["in_telegram"]]
    routine = [f for f in filings if not f["notable"] and not f["in_telegram"]]
    out = [f"## OpenDART 공시 대조 — {result['since']} ~ {result['asof']}", ""]
    out.append(f"- 🆕 텔레그램 미수록 주요 {len(missed)}건")
    for f in missed:
        out.append(f"  - [{f['source']}] {f['name']} {_day(f['rcept_no'])} {f['report_nm']} · {f['rcept_no']}")
    for label, rows in (("📨 텔레그램 수록", forwarded), ("일상 공시", routine)):
        out.append(f"- {label} {len(rows)}건")
        out.extend(f"  - {f['name']} {f['report_nm']} · {f['rcept_no']}" for f in rows)
    if result["gaps"]:
        out.append(f"- 미대조 {len(result['gaps'])}종목: " + " · ".join(result["gaps"]))
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--asof", help="기준일 YYYY-MM-DD (기본 로컬 오늘 = raw·링크 폴더 날짜; 공시 목록은 KST 오늘까지)")
    parser.add_argument("--snapshot", type=Path, help="포트폴리오 스냅샷 (기본 최신)")
    parser.add_argument("--watchlist", type=Path, help="관심 종목 파일 (기본 context/interested_stocks.md)")
    parser.add_argument("--breakout", type=Path, help="돌파선 감시 파일 (기본 output/watchlist/돌파선_감시.md)")
    parser.add_argument("--archive", type=Path, help="브리핑 아카이브 루트 (기본 output/daily-digest)")
    parser.add_argument("--raw", type=Path, help="오늘 raw (기본 output/daily-digest/raw/<오늘>_raw.md)")
    parser.add_argument("--links", type=Path, help="오늘 링크 폴더 (기본 output/daily-digest/links/<오늘>)")
    parser.add_argument("--json", action="store_true", help="JSON으로 출력")
    args = parser.parse_args(argv)

    asof = date.fromisoformat(args.asof) if args.asof else briefing_today()
    since = window_start(args.archive or output_dir() / "daily-digest", asof)
    snapshot = args.snapshot or latest_snapshot()
    codes, gaps = universe(
        [
            ("보유", held_names(snapshot)),
            ("관심", load_watchlist(args.watchlist or repo_root() / WATCHLIST_REL)),
            ("돌파선", load_breakout(args.breakout or output_dir() / BREAKOUT_REL)),
        ]
    )

    # 접수일은 KST다. `--asof`가 없으면 로컬 날짜가 KST보다 하루 늦을 때도 KST 오늘까지 받는다.
    until = asof if args.asof else max(asof, kst_today())
    filings, err = opendart.fetch_filings(since, until)
    if err:
        print(f"OpenDART 공시 목록 미수집 — {err}", file=sys.stderr)
        return 1

    digest = output_dir() / "daily-digest"
    seen = forwarded_rcept_nos(
        args.raw or digest / "raw" / f"{asof.isoformat()}_raw.md",
        args.links or digest / "links" / asof.isoformat(),
    )
    matched = [
        {
            **codes[f["stock_code"]],
            "rcept_no": f["rcept_no"],
            "report_nm": " ".join(f["report_nm"].split()),
            "notable": any(k in f["report_nm"] for k in NOTABLE_KEYWORDS),
            "in_telegram": f["rcept_no"] in seen,
        }
        for f in filings
        if f.get("stock_code") in codes
    ]
    result = {"since": since.isoformat(), "asof": asof.isoformat(), "filings": matched, "gaps": gaps}
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
