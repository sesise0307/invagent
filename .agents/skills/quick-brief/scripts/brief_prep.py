#!/usr/bin/env python3
"""보유·관심 종목의 현재가와 최근 뉴스를 한 번에 모은다 — `quick-brief` 1단계.

시세는 네이버 현재가(`naver.fetch_quote`)와 금융위 공식 정규장 종가(`fsc.fetch_bars`)만 쓴다.
**StockEasy에는 묻지 않는다** — 티커 해석도 StockEasy 폴백을 끈다. 수집 실패는 비블로킹이며
실패한 칸은 사유를 남긴다. 판정은 하지 않는다.

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
from invagent.datafeed import cache, fsc, naver, tickers
from invagent.datafeed.env import output_dir, repo_root
from invagent.datafeed.snapshot import parse_holdings, to_float

WATCHLIST_REL = Path("context") / "interested_stocks.md"
NEWS_PER_STOCK = 5
FSC_WINDOW_DAYS = 10
SNAPSHOT_DATE_RE = re.compile(r"^# .*?(\d{4}-\d{2}-\d{2})", re.M)


def kst_today() -> date:
    """KST 오늘. 실행 기기가 KST가 아니어도(2026-10-20 CEST 실행) 한국 장 날짜를 쓴다."""
    return datetime.fromtimestamp(cache._now(), cache.KST).date()


def latest_snapshot() -> Path | None:
    """`output/portfolio/` 아래 가장 최근 스냅샷. 없으면 None."""
    files = dated_files(output_dir() / "portfolio")
    return files[-1] if files else None


def snapshot_date(path: Path | None) -> str | None:
    """스냅샷 머리줄의 날짜. 머리줄에 없으면 파일 이름."""
    if path is None:
        return None
    match = SNAPSHOT_DATE_RE.search(path.read_text(encoding="utf-8"))
    return match.group(1) if match else path.stem


def load_watchlist(path: Path) -> list[str]:
    """`- 종목명` 줄만 읽는다."""
    if not path.is_file():
        return []
    return [
        line[2:].strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("- ") and line[2:].strip()
    ]


def holdings(path: Path | None) -> list[dict]:
    """스냅샷 보유 행 → `{name, quantity, average_cost, weight}`."""
    if path is None:
        return []
    out = []
    for row in parse_holdings(path.read_text(encoding="utf-8"), require_rows=False):
        out.append(
            {
                "name": row.get("종목") or row.get("종목명"),
                "quantity": to_float(row.get("보유")),
                "average_cost": to_float(row.get("평단")),
                "weight": row.get("비중") or None,
            }
        )
    return out


def collect(name: str, held: dict | None, since: str) -> dict:
    """종목 하나의 시세·공식 종가·뉴스. 빠진 값은 `errors`에 사유로 남긴다."""
    stock = {
        "name": name,
        "code": None,
        "held": held is not None,
        "quantity": held["quantity"] if held else None,
        "average_cost": held["average_cost"] if held else None,
        "weight": held["weight"] if held else None,
        "price": None,
        "change_pct": None,
        "market_status": None,
        "return_pct": None,
        "official_close": None,
        "news": [],
        "errors": [],
    }
    hit, err, _ = tickers.resolve_stock(name, stockeasy_fallback=False)
    if not hit:
        stock["errors"].append(f"티커: {err}")
        return stock
    code = stock["code"] = hit["stock_code"]

    quote, err = naver.fetch_quote(code)
    if quote:
        stock.update(price=quote["price"], change_pct=quote["change_pct"], market_status=quote["market_status"])
        if stock["average_cost"]:
            stock["return_pct"] = (quote["price"] / stock["average_cost"] - 1) * 100
    else:
        stock["errors"].append(f"현재가: {err}")

    if fsc.load_key():
        bars, err = fsc.fetch_bars(code, FSC_WINDOW_DAYS)
        if bars:
            stock["official_close"] = {"date": bars[-1]["date"], "close": bars[-1]["close"]}
        else:
            stock["errors"].append(f"공식 종가: {err or '빈 응답'}")

    news, err = naver.fetch_news(code, count=NEWS_PER_STOCK * 2)
    if err:
        stock["errors"].append(f"뉴스: {err}")
    stock["news"] = [n for n in news if n["datetime"][:8] >= since][:NEWS_PER_STOCK]
    return stock


def _won(value: float | None) -> str:
    return "미수집" if value is None else f"{value:,.0f}"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:+.2f}%"


def render(result: dict) -> str:
    """세션에 바로 붙일 마크다운 — 시세 표, 뉴스, 미수집 사유."""
    lines = [
        f"기준일 {result['asof']} · 스냅샷 {result['snapshot_date'] or '없음'} · 뉴스 {result['since']}~",
        "",
        "| 구분 | 종목 | 현재가 | 등락 | 평단 대비 | 비중 | 공식 종가 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for s in result["stocks"]:
        official = s["official_close"]
        official_text = f"{official['close']:,.0f} ({official['date'][4:]})" if official else "—"
        lines.append(
            f"| {'보유' if s['held'] else '관심'} | {s['name']} | {_won(s['price'])} | {_pct(s['change_pct'])} "
            f"| {_pct(s['return_pct'])} | {s['weight'] or '—'} | {official_text} |"
        )
    lines += ["", "## 뉴스"]
    for s in result["stocks"]:
        for n in s["news"]:
            lines.append(f"- {s['name']} · {n['datetime'][4:8]} {n['office']} · {n['title']}")
    gaps = [f"- {s['name']}: {'; '.join(s['errors'])}" for s in result["stocks"] if s["errors"]]
    if gaps:
        lines += ["", "## 미수집", *gaps]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=2, help="뉴스 기간(일, 기준일 포함 이전)")
    parser.add_argument("--asof", help="기준일 YYYY-MM-DD (기본 KST 오늘)")
    parser.add_argument("--snapshot", type=Path, help="포트폴리오 스냅샷 (기본 최신)")
    parser.add_argument("--watchlist", type=Path, help="관심 종목 파일 (기본 context/interested_stocks.md)")
    parser.add_argument("--json", action="store_true", help="JSON으로 출력")
    args = parser.parse_args(argv)

    asof = date.fromisoformat(args.asof) if args.asof else kst_today()
    since = (asof - timedelta(days=args.days)).strftime("%Y%m%d")
    snapshot = args.snapshot or latest_snapshot()
    held = {h["name"]: h for h in holdings(snapshot)}
    names = list(held) + [
        n for n in dict.fromkeys(load_watchlist(args.watchlist or repo_root() / WATCHLIST_REL)) if n not in held
    ]
    if not names:
        print("보유·관심 종목이 없다", file=sys.stderr)
        return 1

    result = {
        "asof": asof.isoformat(),
        "since": since,
        "snapshot_date": snapshot_date(snapshot),
        "stocks": [collect(name, held.get(name), since) for name in names],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else render(result))
    if all(s["price"] is None for s in result["stocks"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
