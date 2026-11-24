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
from datetime import date, datetime, time, timedelta
from pathlib import Path

from invagent.core.archive import dated_files
from invagent.datafeed import cache, fsc, naver, tickers
from invagent.datafeed.env import output_dir, repo_root
from invagent.datafeed.snapshot import holding_name, parse_holdings, parse_rule_checks, to_float

WATCHLIST_REL = Path("context") / "interested_stocks.md"
# 트리거 점검 범위. 관심 목록이 아니라 이 두 파일에 적힌 종목이다.
TRIGGER_FILES = ("보유_트리거.md", "매수_트리거.md")
TRIGGER_HEADING_RE = re.compile(r"^## (.+?) \((\d{5}[0-9A-Z])\)")
TRIGGER_TABLE_HEADING = "## 감시 대상"
TABLE_NAME_RE = re.compile(r"^(.+?)\s*(?:\((\d{5}[0-9A-Z])\))?$")
# 보유·트리거 종목 전부 고개 들기를 `turn_scan`으로 본다(2026-11-23 사용자 결정).
# 장중: 과거 일봉은 금융위 공식 봉만(`--official`), 오늘은 네이버 현재가(`--price`), 공식 봉 미공개 전일은 네이버 「전일 종가」
# (`--prev-close` = 현재가 − 전일 대비, 금융위 대조 4/4 일치) — StockEasy를 쓰지 않는다.
# 마감 뒤: 네이버 현재가는 시간외가다(11/23 레인보우 412,000 vs 정규장 402,000) → 기본 판정 일봉(StockEasy 정규장 종가)으로 돌린다.
TURN_SCAN_CMD = "uv run python .agents/skills/stage-analysis/scripts/turn_scan.py"
NEWS_PER_STOCK = 5
FSC_WINDOW_DAYS = 10
REGULAR_SESSION_OPEN = time(9, 0)
SESSION_LABELS = {"intraday": "장중(가격선 판정 미확정)", "closed": "정규장 마감(종가 판정 가능)"}
SNAPSHOT_DATE_RE = re.compile(r"^# .*?(\d{4}-\d{2}-\d{2})", re.M)

# 시장 블록. 지수 일봉·현재가는 네이버만 쓴다 — `fetch_market_signals.py`는 StockEasy를 타서 못 쓴다.
# 국면·레버리지 임계값은 그 스크립트와 같은 숫자다(`test_agent_configuration.py`가 묶는다).
# 클라이맥스는 반대매매(StockEasy) 없이는 판정할 수 없어 내지 않는다.
INDEX_CODES = ("KOSPI", "KOSDAQ")
INDEX_WINDOW_DAYS = 400       # 52주 고점(250거래일)과 200일선을 채우는 달력일
ACCOUNT_RULE1_MA_DAYS = 3     # = MDD_RELEASE_MA_DAYS — 「계좌 규칙 1」 해제
SHORT_MA_DAYS = 5             # 단기 반등 확인 (참고)
TREND_MA_DAYS = 20            # 단기 추세, 전환일 알림
MID_MA_DAYS = 60              # 중기 추세 회복 확인 (참고)
BREAKOUT_MA_DAYS = 200        # = BREAKOUT_REGIME_MA_DAYS — 「매매 규칙 3」 시장 필터
MA_DAYS = (ACCOUNT_RULE1_MA_DAYS, SHORT_MA_DAYS, TREND_MA_DAYS, MID_MA_DAYS, BREAKOUT_MA_DAYS)
PEAK_WINDOW = 250             # = MARGIN_CALL_PEAK_WINDOW
CORRECTION_DRAWDOWN_PCT = -10.0  # = MARGIN_CALL_MIN_DRAWDOWN_PCT
BEAR_DRAWDOWN_PCT = -20.0
CRASH_WINDOW_DAYS = 3
CRASH_DROP_PCT = -3.0
CRASH_CUMULATIVE_PCT = -8.0
CRASH_SINGLE_DAY_PCT = -5.0
VOLATILE_DAY_PCT = 5.0
VOLATILE_WINDOW_DAYS = 10
VOLATILE_DAY_TRIGGER = 3


def kst_now() -> datetime:
    return datetime.fromtimestamp(cache._now(), cache.KST)


def kst_today() -> date:
    """KST 오늘. 실행 기기가 KST가 아니어도(2026-10-20 CEST 실행) 한국 장 날짜를 쓴다."""
    return kst_now().date()


def session(now: datetime) -> str:
    """`intraday` — 평일 09:00~15:40 KST라 현재가가 종가가 아니다. 그 밖은 `closed`(정규장 종가 확정).

    공휴일은 모른다 — 휴장 평일 낮은 `intraday`로 나와 판정을 미루는 쪽으로 틀린다.
    """
    if now.weekday() < 5 and REGULAR_SESSION_OPEN <= now.time() < cache.REGULAR_SESSION_SETTLED:
        return "intraday"
    return "closed"


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


def trigger_stocks(path: Path) -> list[tuple[str, str | None]]:
    """트리거 파일의 종목 — `## 종목명 (티커)` 절과 「감시 대상」 표의 첫 칸. 티커가 없으면 None."""
    if not path.is_file():
        return []
    found: list[tuple[str, str | None]] = []
    in_table = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            in_table = line.strip() == TRIGGER_TABLE_HEADING
            match = TRIGGER_HEADING_RE.match(line)
            if match:
                found.append((match.group(1).strip(), match.group(2)))
            continue
        if in_table and line.startswith("|"):
            cell = line.strip("|").split("|")[0].strip()
            if cell and cell != "종목" and not set(cell) <= set("-: "):
                match = TABLE_NAME_RE.match(cell)
                found.append((match.group(1).strip(), match.group(2)))
    return found


def rule_checks(path: Path | None) -> list[str]:
    return parse_rule_checks(path.read_text(encoding="utf-8")) if path else []


def holdings(path: Path | None) -> list[dict]:
    """스냅샷 보유 행 → `{name, quantity, average_cost, weight}`."""
    if path is None:
        return []
    out = []
    for row in parse_holdings(path.read_text(encoding="utf-8"), require_rows=False):
        out.append(
            {
                "name": holding_name(row),
                "quantity": to_float(row.get("보유")),
                "average_cost": to_float(row.get("평단")),
                "weight": row.get("비중") or None,
            }
        )
    return out


def collect(
    name: str, held: dict | None, since: str, code: str | None = None, trigger: bool = False, watched: bool = False,
    intraday: bool = False,
) -> dict:
    """종목 하나의 시세·공식 종가·뉴스. 빠진 값은 `errors`에 사유로 남긴다.

    `code`가 있으면(트리거 파일에 적힌 티커) 이름 검색을 건너뛴다.
    """
    stock = {
        "name": name,
        "code": None,
        "held": held is not None,
        "watched": watched,
        "trigger": trigger,
        "quantity": held["quantity"] if held else None,
        "average_cost": held["average_cost"] if held else None,
        "weight": held["weight"] if held else None,
        "price": None,
        "change_pct": None,
        "market_status": None,
        "return_pct": None,
        "official_close": None,
        "turn_scan": None,
        "news": [],
        "errors": [],
    }
    if code is None:
        hit, err, _ = tickers.resolve_stock(name, stockeasy_fallback=False)
        if not hit:
            stock["errors"].append(f"티커: {err}")
            return stock
        code = hit["stock_code"]
    stock["code"] = code

    quote, err = naver.fetch_quote(code)
    if quote:
        stock.update(price=quote["price"], change_pct=quote["change_pct"], market_status=quote["market_status"])
        if stock["average_cost"]:
            stock["return_pct"] = (quote["price"] / stock["average_cost"] - 1) * 100
        traded_on = str(quote.get("traded_at") or "")[:10]
        if (held is not None or trigger) and intraday and traded_on:
            command = f"{TURN_SCAN_CMD} {code} --official --price {quote['price']:.0f} --price-date {traded_on}"
            if quote.get("change") is not None:
                command += f" --prev-close {quote['price'] - quote['change']:.0f}"
            stock["turn_scan"] = command
    else:
        stock["errors"].append(f"현재가: {err}")
    if (held is not None or trigger) and not intraday:
        stock["turn_scan"] = f"{TURN_SCAN_CMD} {code}"

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


def order_stocks(stocks: list[dict]) -> list[dict]:
    """보유는 받은 순서 그대로 앞에, 관심·트리거 전용은 등락률 내림차순(현재가 미수집은 끝)."""
    held = [s for s in stocks if s["held"]]
    rest = sorted(
        (s for s in stocks if not s["held"]),
        key=lambda s: (s["change_pct"] is None, -(s["change_pct"] or 0.0)),
    )
    return held + rest


def with_today(bars: list[dict], quote: dict | None, asof: date) -> list[float]:
    """일봉 종가에 오늘 현재가를 덮어쓰거나 붙인다. 현재가가 오늘 것이 아니면 일봉 그대로."""
    closes = [(b["date"], b["close"]) for b in bars if b["date"] <= asof.strftime("%Y%m%d")]
    if quote and str(quote.get("traded_at") or "")[:10] == asof.isoformat():
        today = asof.strftime("%Y%m%d")
        closes = [c for c in closes if c[0] != today] + [(today, quote["price"])]
    return [close for _, close in closes]


def _average(closes: list[float], days: int) -> float | None:
    return sum(closes[-days:]) / days if len(closes) >= days else None


def _changes(closes: list[float]) -> list[float]:
    return [(cur / prev - 1) * 100 for prev, cur in zip(closes, closes[1:]) if prev]


def regime(closes: list[float]) -> tuple[str, float | None]:
    """「시장 국면」(클라이맥스 제외): crash > bear > correction > normal, 그리고 52주 고점 대비 낙폭 %."""
    window = closes[-PEAK_WINDOW:]
    if not window:
        return "unknown", None
    drawdown = (window[-1] / max(window) - 1) * 100
    deep = drawdown <= CORRECTION_DRAWDOWN_PCT
    recent = _changes(closes)[-CRASH_WINDOW_DAYS:]
    if deep and recent and recent[-1] <= CRASH_SINGLE_DAY_PCT:
        return "crash", drawdown
    if deep and len(recent) == CRASH_WINDOW_DAYS:
        growth = 1.0
        for v in recent:
            growth *= 1 + v / 100
        if recent[-1] < CRASH_DROP_PCT and (growth - 1) * 100 <= CRASH_CUMULATIVE_PCT:
            return "crash", drawdown
    if drawdown <= BEAR_DRAWDOWN_PCT:
        return "bear", drawdown
    return ("correction" if deep else "normal"), drawdown


def leverage_rule3(closes: list[float]) -> str:
    """「레버리지 규칙 3」 — `ok`·`fired`·`exempt`·`insufficient`."""
    window = _changes(closes)[-VOLATILE_WINDOW_DAYS:]
    if len(window) < VOLATILE_WINDOW_DAYS:
        return "insufficient"
    if sum(abs(v) >= VOLATILE_DAY_PCT for v in window) < VOLATILE_DAY_TRIGGER:
        return "ok"
    span = closes[-(VOLATILE_WINDOW_DAYS + 1):]
    if min(span[1:]) >= span[0] and span[-1] > span[0]:
        return "exempt"
    return "fired"


def index_view(bars: list[dict], quote: dict | None, asof: date) -> dict:
    """지수 하나의 이평선 위치·20일선 전환·52주 고점 대비 낙폭·국면·레버리지 규칙 3."""
    closes = with_today(bars, quote, asof)
    above = {}
    for days in MA_DAYS:
        average = _average(closes, days)
        above[days] = None if average is None else closes[-1] > average
    cross = None
    before = _average(closes[:-1], TREND_MA_DAYS)
    if above[TREND_MA_DAYS] is not None and before is not None:
        was_above = closes[-2] > before
        if was_above != above[TREND_MA_DAYS]:
            cross = "up" if above[TREND_MA_DAYS] else "down"
    state, drawdown = regime(closes)
    return {
        "close": closes[-1] if closes else None,
        "change_pct": quote.get("change_pct") if quote else None,
        "above": above,
        "ma20_cross": cross,
        "drawdown": drawdown,
        "regime": state,
        "leverage_rule3": leverage_rule3(closes),
    }


def market(asof: date) -> dict:
    """KOSPI·KOSDAQ 판정. 실패는 `errors`에."""
    indices, errors = {}, []
    for code in INDEX_CODES:
        bars, err = naver.fetch_bars(code, INDEX_WINDOW_DAYS, asof)
        if not bars:
            errors.append(f"{code} 일봉: {err or '빈 응답'}")
            continue
        quote, err = naver.fetch_index_quote(code)
        if err:
            errors.append(f"{code} 현재가: {err}")
        indices[code] = index_view(bars, quote, asof)
    return {"indices": indices, "errors": errors}


def _won(value: float | None) -> str:
    return "미수집" if value is None else f"{value:,.0f}"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:+.2f}%"


REGIME_KO = {"crash": "🟠 급락", "bear": "🔴 약세", "correction": "🟡 조정", "normal": "정상", "unknown": "미상"}
LEVERAGE_KO = {"ok": "미발동", "fired": "⛔ 발동", "exempt": "예외(저점 유지·상승)", "insufficient": "판정 불가"}


def _mark(value: bool | None) -> str:
    return "—" if value is None else ("✅" if value else "❌")


def render_market(block: dict) -> list[str]:
    """지수 표와 룰 연결 줄. 클라이맥스는 반대매매가 없어 내지 않는다."""
    lines = [
        "| 지수 | 현재 | 등락 | " + " | ".join(f"{d}일선" for d in MA_DAYS) + " | 고점 대비 | 국면 |",
        "| --- | ---: | ---: | " + " | ".join(":-:" for _ in MA_DAYS) + " | ---: | :-: |",
    ]
    for code, v in block["indices"].items():
        dd = "—" if v["drawdown"] is None else f"{v['drawdown']:+.1f}%"
        lines.append(
            f"| {code} | {v['close']:,.2f} | {_pct(v['change_pct'])} | "
            + " | ".join(_mark(v["above"][d]) for d in MA_DAYS)
            + f" | {dd} | {REGIME_KO[v['regime']]} |"
        )
    indices = block["indices"]
    lines += [
        "",
        "- 매매 규칙 3 (200일선 위): " + " · ".join(f"{c} {_mark(v['above'][BREAKOUT_MA_DAYS])}" for c, v in indices.items()),
        "- 레버리지 규칙 3: " + " · ".join(f"{c} {LEVERAGE_KO[v['leverage_rule3']]}" for c, v in indices.items()),
    ]
    crosses = [f"{c} 20일선 {'상향 돌파' if v['ma20_cross'] == 'up' else '하회'} 전환"
               for c, v in indices.items() if v["ma20_cross"]]
    if crosses:
        lines.append("- 20일선 전환: " + " · ".join(crosses))
    lines.append("- 클라이맥스: 미확인 (반대매매 = daily-digest)")
    lines += [f"- 미수집 {e}" for e in block["errors"]]
    return lines


def _kind(stock: dict) -> str:
    if stock["held"]:
        return "보유"
    return "관심" if stock.get("watched") else "트리거"


def render(result: dict) -> str:
    """세션에 바로 붙일 마크다운 — 시장 표, 시세 표, 뉴스, 미수집 사유."""
    lines = [
        f"기준일 {result['asof']} · {SESSION_LABELS[result['session']]} · 스냅샷 {result['snapshot_date'] or '없음'} · 뉴스 {result['since']}~",
        "",
        *render_market(result["market"]),
        "",
        "| 구분 | 종목 | 현재가 | 등락 | 평단 대비 | 비중 | 공식 종가 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for s in result["stocks"]:
        official = s["official_close"]
        official_text = f"{official['close']:,.0f} ({official['date'][4:]})" if official else "—"
        lines.append(
            f"| {_kind(s)} | {s['name']} | {_won(s['price'])} | {_pct(s['change_pct'])} "
            f"| {_pct(s['return_pct'])} | {s['weight'] or '—'} | {official_text} |"
        )
    if result["rule_checks"]:
        lines += ["", "## 룰 자동 판정 (스냅샷)", *(f"- {line}" for line in result["rule_checks"])]
    scans = [f"- {s['name']}: `{s['turn_scan']}`" for s in result["stocks"] if s.get("turn_scan")]
    if scans:
        lines += ["", "## 턴스캔 (보유·트리거)", *scans]
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
    parser.add_argument(
        "--triggers", type=Path, action="append",
        help="트리거 파일 (반복 가능, 기본 output/watchlist/보유_트리거.md·매수_트리거.md)",
    )
    parser.add_argument("--json", action="store_true", help="JSON으로 출력")
    args = parser.parse_args(argv)

    asof = date.fromisoformat(args.asof) if args.asof else kst_today()
    since = (asof - timedelta(days=args.days)).strftime("%Y%m%d")
    snapshot = args.snapshot or latest_snapshot()
    held = {h["name"]: h for h in holdings(snapshot)}
    trigger_paths = args.triggers or [output_dir() / "watchlist" / f for f in TRIGGER_FILES]
    triggered: dict[str, str | None] = {}
    for path in trigger_paths:
        for name, code in trigger_stocks(path):
            triggered[name] = triggered.get(name) or code
    watched = set(load_watchlist(args.watchlist or repo_root() / WATCHLIST_REL))
    names = list(held) + [
        n for n in dict.fromkeys(load_watchlist(args.watchlist or repo_root() / WATCHLIST_REL)) if n not in held
    ]
    names += [n for n in triggered if n not in names]
    if not names:
        print("보유·관심 종목이 없다", file=sys.stderr)
        return 1

    now_session = session(kst_now())
    result = {
        "asof": asof.isoformat(),
        "session": now_session,
        "since": since,
        "snapshot_date": snapshot_date(snapshot),
        "rule_checks": rule_checks(snapshot),
        "market": market(asof),
        "stocks": order_stocks([
            collect(name, held.get(name), since, code=triggered.get(name), trigger=name in triggered,
                    watched=name in watched, intraday=now_session == "intraday")
            for name in names
        ]),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else render(result))
    if all(s["price"] is None for s in result["stocks"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
