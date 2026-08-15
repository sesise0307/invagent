#!/usr/bin/env python3
"""StockEasy 종목정보 수집기.

stockeasy.intellio.kr의 `/stockdata/api/v1/**` JSON API에서 개별 종목의 시세·멀티플·
증권사별 목표주가 추이·컨센서스 추정 실적·뉴스를 받아 압축 요약을 출력한다.

종목정보 페이지(`/stock-analysis/stock-info/<ticker>`)는 클라이언트 렌더링이라 HTML에
데이터가 없다. `fetch_market_signals.py`와 같은 방식으로 페이지가 실제 호출하는 API를 친다.

`info-tab` 원본 응답은 128KB 규모(3년치 차트 포함)라 그대로 읽으면 컨텍스트가 마른다.
차트 계열은 버리고 판단에 쓰는 값만 남긴다.

증권사 리포트 **요약 본문**(`securities-reports`)만 로그인 세션이 필요하다. 브라우저에서 복사한
Cookie 헤더를 `.env`의 `STOCKEASY_COOKIE`에 넣으면 이 스크립트가 함께 받아온다. 값이 없거나
만료되면 리포트 섹션만 비고 나머지는 정상 출력한다(비블로킹).

의존성: stdlib만 사용 (urllib, json, argparse).
종료 코드: 0 정상(부분 누락 포함) / 1 수집 실패 / 2 종목명 후보 다수.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

API_BASE = "https://stockeasy.intellio.kr/stockdata/api/v1"
PAGE_BASE = "https://stockeasy.intellio.kr/stock-analysis/stock-info"
REPORTS_PAGE = "https://stockeasy.intellio.kr/stock-analysis/reports"
ENDPOINTS = {
    "search": "/stock-search/",
    "info_tab": "/stock-info/info-tab/{code}",
    "news": "/news/by-stock-code/{code}",
    "reports": "/securities-reports",
}
TIMEOUT = 20
TICKER_RE = re.compile(r"^\d{6}$")
COOKIE_ENV = "STOCKEASY_COOKIE"

# 추정치는 컨센서스이지 공시가 아니다. 표 헤더에 그대로 박아 보고서로 흘려보낸다.
ESTIMATE_NOTE = "추정 — 컨센서스, DART 데이터 아님"


def load_cookie() -> str | None:
    """`STOCKEASY_COOKIE`를 환경변수 → 저장소 루트 `.env` 순으로 찾는다.

    저장소는 dotenv 라이브러리를 쓰지 않으므로 `.env`를 직접 훑는다.
    값(쿠키 본문)은 출력하지 않는다 — 로그·보고서로 새면 안 된다.
    """
    value = os.environ.get(COOKIE_ENV, "").strip()
    if value:
        return value

    for parent in Path(__file__).resolve().parents:
        env_path = parent / ".env"
        if not env_path.is_file():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, raw = line.partition("=")
            if key.strip() == COOKIE_ENV:
                return raw.strip().strip("'\"") or None
        break
    return None


def fetch_json(
    path: str,
    params: dict | None = None,
    referer: str = PAGE_BASE,
    cookie: str | None = None,
):
    """API 하나를 호출해 JSON을 반환한다. 실패하면 (None, 사유)."""
    url = API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Referer": referer,
    }
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except Exception as e:  # 네트워크 오류·JSON 파싱 실패 등
        return None, str(e)[:80]


# --- 값 정리 ---------------------------------------------------------------


def unsigned(value) -> float | None:
    """`stock_info` 가격 필드의 등락 방향 부호를 떼고 절대값을 돌려준다.

    API는 `cur_prc: "-30850"`처럼 **가격에 등락 방향을 부호로 박는다.** 그대로 쓰면
    주가가 음수로 나온다. 방향은 `flu_rt`·`pred_pre`로 따로 읽는다.
    """
    if value in (None, "", "-"):
        return None
    try:
        return abs(float(str(value).replace(",", "")))
    except ValueError:
        return None


def signed(value) -> float | None:
    """등락률처럼 부호가 의미를 갖는 필드용."""
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", "").replace("+", ""))
    except ValueError:
        return None


def won(value) -> str:
    v = unsigned(value)
    return "-" if v is None else f"{v:,.0f}원"


def pct(value, plus: bool = True) -> str:
    v = signed(value)
    if v is None:
        return "-"
    return f"{v:+.2f}%" if plus else f"{v:.2f}%"


def eok(value) -> str:
    """원 단위 금액을 억원으로."""
    if value is None:
        return "-"
    return f"{value / 1e8:,.0f}억원"


def ymd(raw) -> str:
    s = str(raw or "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else (s or "-")


# --- 종목 해석 -------------------------------------------------------------


def resolve_stock(query: str):
    """종목명 또는 6자리 티커 → (code, name, market). 실패하면 (None, 사유, exit_code)."""
    if TICKER_RE.match(query):
        return {"stock_code": query, "stock_name": None, "exchange": None}, None, 0

    data, err = fetch_json(ENDPOINTS["search"], {"q": query})
    if err:
        return None, f"종목 검색 실패 — {err}", 1
    hits = [h for h in (data or []) if h.get("market") == "KR"] or (data or [])
    if not hits:
        return None, f"종목 검색 결과 없음 — '{query}'", 1

    exact = [h for h in hits if h.get("stock_name") == query]
    if len(exact) == 1:
        return exact[0], None, 0
    if len(hits) == 1:
        return hits[0], None, 0

    candidates = ", ".join(
        f"{h.get('stock_name')}({h.get('stock_code')}, {h.get('exchange')})" for h in hits[:10]
    )
    return None, f"종목명 후보 다수 — {candidates}", 2


# --- 섹션 출력 -------------------------------------------------------------


def num(value, suffix: str = "", fmt: str = ",.0f") -> str:
    v = signed(value)
    return "-" if v is None else f"{v:{fmt}}{suffix}"


def print_quote(si: dict) -> None:
    print(
        f"[시세] {won(si.get('cur_prc'))} "
        f"({pct(si.get('flu_rt'))}, 전일대비 {num(si.get('pred_pre'), '원', '+,.0f')}) | "
        f"시총 {num(si.get('mac'), '억원')} | "
        f"거래량 {num(si.get('trde_qty'), '주')}"
    )


def print_multiples(si: dict) -> None:
    # EPS·ROE의 부호는 실제 손익 방향이다(가격 필드의 등락 방향 마커와 다르다). 그대로 둔다.
    print(
        f"[멀티플] PER {si.get('per') or '-'} / PBR {si.get('pbr') or '-'} / "
        f"EPS {num(si.get('eps'), '원')} / BPS {won(si.get('bps'))} / "
        f"ROE {num(si.get('roe'), '%', '.1f')}"
    )


def print_52w(si: dict) -> None:
    print(
        f"[52주(250일)] 고 {won(si.get('250hgst'))}({ymd(si.get('250hgst_pric_dt'))}, "
        f"{pct(si.get('250hgst_pric_pre_rt'))}) / "
        f"저 {won(si.get('250lwst'))}({ymd(si.get('250lwst_pric_dt'))}, "
        f"{pct(si.get('250lwst_pric_pre_rt'))})"
    )
    hi, lo, cur = unsigned(si.get("250hgst")), unsigned(si.get("250lwst")), unsigned(si.get("cur_prc"))
    if hi and lo and cur and hi > lo:
        print(f"[52주 밴드 내 위치] {(cur - lo) / (hi - lo) * 100:.1f}% (0%=저점, 100%=고점)")


def print_flow(si: dict) -> None:
    shares = signed(si.get("total_shares")) or signed(si.get("flo_stk"))
    print(
        f"[수급] 외국인 {num(si.get('for_exh_rt'), '%', '.2f')} · "
        f"유통 {num(si.get('dstr_rt'), '%', '.1f')} · "
        f"상장주식 {'-' if shares is None else format(shares, ',.0f') + '주'} · "
        f"시장 {si.get('market') or '-'}"
    )


def print_sector(sector: dict | None, rs: dict | None) -> None:
    if not sector and not rs:
        return
    sector = sector or {}
    rs = rs or {}
    line = f"[섹터] {sector.get('major_name') or '-'} > {sector.get('mid_name') or '-'}"
    if rs:
        line += (
            f" | RS {rs.get('rs')} (1M {rs.get('rs_1m')} / 3M {rs.get('rs_3m')} / 6M {rs.get('rs_6m')})"
        )
    print(line)


def print_grades(inv: dict | None) -> None:
    if not inv:
        return
    basis = "연결" if inv.get("financial_type") == "C" else "별도"
    parts = []
    for key in ("growth", "profitability", "stability", "valuation"):
        cat = inv.get(key) or {}
        if cat:
            parts.append(f"{cat.get('category_name')} {cat.get('average_grade')}")
    print(
        f"[투자지표 등급] {' · '.join(parts)} · 종합 {inv.get('overall_grade') or '-'}"
        f"  (기준 {inv.get('base_period') or '-'}, {basis})"
    )
    for key in ("growth", "profitability", "stability", "valuation"):
        cat = inv.get(key) or {}
        metrics = [
            f"{m['name']} {m.get('formatted_value')}({m.get('grade')})" for m in cat.get("metrics", [])
        ]
        if metrics:
            print(f"  - {cat.get('category_name')}: {' · '.join(metrics)}")


def print_target_prices(history: list, current_price: float | None, limit: int, since: str | None) -> None:
    rows = sorted(history or [], key=lambda r: r.get("report_date") or "", reverse=True)
    print(f"[증권사별 목표주가] 총 {len(rows)}건" + (f" (최근 {limit}건 표시)" if len(rows) > limit else ""))
    if not rows:
        print("  (없음)")
        return
    print("  | 발간일 | 증권사 | 애널리스트 | 의견 | 목표가 | 변동 | 당시주가 | 괴리율 | 리포트 |")
    print("  |---|---|---|---|---|---|---|---|---|")
    for r in rows[:limit]:
        tp = r.get("target_price")
        cp = r.get("current_price")
        up = r.get("upside_potential")
        new_mark = " 🆕" if since and (r.get("report_date") or "") > since else ""
        print(
            f"  | {r.get('report_date') or '-'}{new_mark} | {r.get('securities_company') or '-'} | "
            f"{r.get('author') or '-'} | {r.get('investment_opinion') or '-'} | "
            f"{format(tp, ',') + '원' if tp else '-'} | {r.get('target_price_change') or '-'} | "
            f"{format(cp, ',') + '원' if cp else '-'} | "
            f"{format(up, '+.1f') + '%' if up is not None else '-'} | {r.get('title') or '-'} |"
        )

    rated = [r for r in rows if r.get("target_price")]
    if not rated:
        print("[컨센 요약] 목표가 제시 리포트 없음 (전부 Not Rated)")
        return
    targets = [r["target_price"] for r in rated]
    avg = sum(targets) / len(targets)
    brokers = {r.get("securities_company") for r in rated}
    ups = sum(1 for r in rows if r.get("target_price_change") == "상향")
    downs = sum(1 for r in rows if r.get("target_price_change") == "하향")
    upside = f" · 현재가 대비 {(avg - current_price) / current_price * 100:+.1f}%" if current_price else ""
    print(
        f"[컨센 요약] 평균 목표가 {avg:,.0f}원 (최고 {max(targets):,.0f} / 최저 {min(targets):,.0f}) · "
        f"커버 {len(brokers)}사 / 목표가 제시 {len(rated)}건 · 상향 {ups} / 하향 {downs}{upside}"
    )


def _fs_rows(financials: dict, primary: str, yearly: bool) -> tuple[list, list]:
    """primary_fs_type(C/S)에 맞는 (확정, 추정) 리스트."""
    prefix = "consolidated" if primary != "S" else "separate"
    if yearly:
        return financials.get(f"{prefix}Yearly") or [], financials.get(f"{prefix}YearlyEstimate") or []
    return financials.get(prefix) or [], financials.get(f"{prefix}Estimate") or []


def _period(row: dict, yearly: bool) -> str:
    if yearly:
        return f"{row.get('year')}"
    return f"{row.get('year')}.{row.get('quarter')}Q"


def print_financials(financials: dict | None, primary: str, yearly: bool, actual_n: int) -> None:
    if not financials:
        return
    actual, estimate = _fs_rows(financials, primary, yearly)
    if not actual and not estimate:
        return
    basis = "연결" if primary != "S" else "별도"
    label = "연간" if yearly else "분기"
    print(f"[실적·컨센 추정 — {label}] 기준 {basis} · E 표시분은 「{ESTIMATE_NOTE}」")
    print("  | 기간 | 구분 | 매출 | 영업이익 | OPM | 순이익 | EPS | PER | PBR |")
    print("  |---|---|---|---|---|---|---|---|---|")
    rows = [(r, "확정") for r in actual[:actual_n]] + [(r, "**추정 E**") for r in estimate]
    rows.sort(key=lambda x: (x[0].get("year") or 0, x[0].get("month") or 0), reverse=True)
    for r, kind in rows:
        opm, eps = r.get("operating_margin"), r.get("eps")
        per, pbr = r.get("per"), r.get("pbr")
        print(
            f"  | {_period(r, yearly)} | {kind} | {eok(r.get('revenue'))} | "
            f"{eok(r.get('operating_income'))} | {'-' if opm is None else format(opm, '.1f') + '%'} | "
            f"{eok(r.get('net_income'))} | {'-' if eps is None else format(eps, ',.0f') + '원'} | "
            f"{'-' if per is None else format(per, '.1f') + '배'} | "
            f"{'-' if pbr is None else format(pbr, '.2f') + '배'} |"
        )


def print_eps_changes(eps: dict | None, limit: int = 5) -> None:
    if not eps or not eps.get("changes"):
        return
    seen, rows = set(), []
    for c in eps["changes"]:
        key = (c.get("quarter"), c.get("value_old"), c.get("value_new"), c.get("change_rate"))
        if key in seen:
            continue
        seen.add(key)
        rows.append(c)
    print(f"[EPS 컨센 변화] 최근 {min(limit, len(rows))}건 (총 {eps.get('count')}건)")
    for c in rows[:limit]:
        print(
            f"  - {c.get('quarter')} {c.get('change_date_str')}: "
            f"{c.get('value_old')} → {c.get('value_new')} ({c.get('change_rate'):+.2f}%)"
        )


TAG_RE = re.compile(r"<[^>]+>")


def _clean(text) -> str:
    return " ".join(TAG_RE.sub(" ", str(text or "")).split())


def _flatten_points(points) -> list[str]:
    """`summary_points`는 `[{"point": "..."}]`로 온다. 문자열 리스트도 받아준다."""
    flat = []
    for p in points or []:
        if isinstance(p, dict):
            head = p.get("header") or p.get("title") or p.get("heading") or ""
            body = p.get("point") or p.get("content") or p.get("text") or p.get("description") or ""
            text = f"{head}: {body}" if head and body else (head or body)
        else:
            text = p
        text = _clean(text)
        if text:
            flat.append(text)
    return flat


def _detail_text(detail) -> str:
    """`detail_content`는 `{"card_news_sections": [{header, content}]}` dict로 온다."""
    if isinstance(detail, dict):
        sections = detail.get("card_news_sections") or detail.get("sections") or []
        return " / ".join(_flatten_points(sections))
    return _clean(detail)


def print_reports(reports: dict | None, limit: int, detail_chars: int) -> None:
    """증권사 리포트 요약. 로그인 쿠키가 있어야 채워진다."""
    items = (reports or {}).get("items") or []
    print(f"[리포트 요약] {min(len(items), limit)}건 (총 {(reports or {}).get('total_count', len(items))}건)")
    if not items:
        print("  (없음)")
        return
    for r in items[:limit]:
        tp = r.get("target_price")
        head = (
            f"  - {r.get('report_date') or '-'} [{r.get('securities_company') or '-'}] "
            f"{r.get('author') or '-'} · {r.get('investment_opinion') or '의견 없음'}"
        )
        if tp:
            head += f" 목표가 {tp:,}원"
            if r.get("target_price_change"):
                head += f"({r['target_price_change']})"
        print(f"{head} — {r.get('title') or '-'}")
        for point in _flatten_points(r.get("summary_points")):
            print(f"      · {point}")
        # 4단계 로컬 PDF와의 중복 판정에 쓴다 (아카이브 파일명과 같은 규칙).
        if r.get("file_name"):
            print(f"      파일: {r['file_name']}")
        detail = _detail_text(r.get("detail_content"))
        if detail_chars and detail:
            print(f"      본문: {detail[:detail_chars]}{'…' if len(detail) > detail_chars else ''}")


def print_news(news: dict | None, since: str | None) -> None:
    items = (news or {}).get("items") or []
    if since:
        items = [n for n in items if (n.get("published_at") or "")[:10] >= since]
    print(f"[뉴스] {len(items)}건" + (f" (기준일 {since} 이후)" if since else ""))
    for n in items:
        published = (n.get("published_at") or "")[:10] or "-"
        print(f"  - {published} [{n.get('source') or '-'}] {n.get('title')}")
        print(f"    {n.get('link') or n.get('original_link') or '-'}")


# --- main ------------------------------------------------------------------


def build_summary(stock: dict, info: dict | None, news: dict | None, reports: dict | None = None) -> dict:
    """--json 출력용 압축 dict. 차트 계열은 버린다."""
    info = info or {}
    return {
        "stock_code": stock.get("stock_code"),
        "stock_name": stock.get("stock_name"),
        "fetched_at": date.today().isoformat(),
        "stock_info": info.get("stock_info"),
        "primary_fs_type": info.get("primary_fs_type"),
        "sector_info": info.get("sector_info"),
        "rs_data": info.get("rs_data"),
        "investment": info.get("investment"),
        "target_price_history": info.get("target_price_history"),
        "financials": info.get("financials"),
        "eps_changes": info.get("eps_changes"),
        "news": (news or {}).get("items"),
        "reports": (reports or {}).get("items"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="StockEasy 종목정보 수집")
    parser.add_argument("query", help="종목명 또는 6자리 티커")
    parser.add_argument("--news", type=int, default=10, help="뉴스 건수 (기본 10)")
    parser.add_argument("--reports", type=int, default=12, help="목표주가 표시 건수 (기본 12)")
    parser.add_argument("--quarters", type=int, default=4, help="확정 분기 표시 개수 (기본 4)")
    parser.add_argument("--years", type=int, default=3, help="확정 연도 표시 개수 (기본 3)")
    parser.add_argument("--since", help="YYYY-MM-DD. 뉴스·리포트는 이 날짜 이후만, 신규 리포트는 🆕 표시")
    parser.add_argument(
        "--summaries", type=int, default=5, help="리포트 요약 건수 (기본 5, 0이면 생략)"
    )
    parser.add_argument(
        "--detail-chars",
        type=int,
        default=0,
        help="리포트 본문(detail_content)을 N자까지 함께 출력 (기본 0=요약만)",
    )
    parser.add_argument("--json", action="store_true", help="압축 JSON 덤프")
    args = parser.parse_args(argv)

    stock, err, code = resolve_stock(args.query)
    if err:
        print(f"ERROR: {err}", file=sys.stderr)
        return code

    ticker = stock["stock_code"]
    referer = f"{PAGE_BASE}/{ticker}"
    errors = {}

    info, e = fetch_json(ENDPOINTS["info_tab"].format(code=ticker), referer=referer)
    if e:
        errors["info_tab"] = e
    news = None
    if args.news:
        news, e = fetch_json(
            ENDPOINTS["news"].format(code=ticker), {"limit": args.news}, referer=referer
        )
        if e:
            errors["news"] = e

    # 리포트 요약만 로그인 세션이 필요하다. 쿠키가 없으면 그 섹션만 비운다 (비블로킹).
    reports = None
    if args.summaries:
        cookie = load_cookie()
        if not cookie:
            errors["reports"] = f"{COOKIE_ENV} 미설정 — .env에 브라우저 Cookie 헤더를 넣어야 한다"
        else:
            params = {"stock_code": ticker, "page": 1, "page_size": max(args.summaries, 10)}
            if args.since:
                params["date_from"] = args.since
            reports, e = fetch_json(
                ENDPOINTS["reports"], params, referer=REPORTS_PAGE, cookie=cookie
            )
            if e:
                hint = " (쿠키 만료·무효 — .env 갱신 필요)" if e == "HTTP 401" else ""
                errors["reports"] = f"{e}{hint}"

    if not info:
        detail = ", ".join(f"{k}={v}" for k, v in errors.items()) or "빈 응답"
        print(f"ERROR: StockEasy 종목정보 호출 실패 — {detail}", file=sys.stderr)
        return 1

    si = info.get("stock_info") or {}
    name = stock.get("stock_name") or si.get("name") or ticker

    if args.json:
        print(
            json.dumps(
                build_summary({**stock, "stock_name": name}, info, news, reports),
                ensure_ascii=False,
            )
        )
        for key, msg in errors.items():
            print(f"[누락] {key} — {msg}", file=sys.stderr)
        return 0

    print(
        f"=== StockEasy 종목정보 — {name}({ticker}) {si.get('market') or ''} === "
        f"조회 {date.today().isoformat()} · 출처 {PAGE_BASE}/{ticker}"
    )
    print_quote(si)
    print_multiples(si)
    print_52w(si)
    print_flow(si)
    print_sector(info.get("sector_info"), info.get("rs_data"))
    print_grades(info.get("investment"))
    print_target_prices(
        info.get("target_price_history") or [], unsigned(si.get("cur_prc")), args.reports, args.since
    )
    primary = info.get("primary_fs_type") or "C"
    print_financials(info.get("financials"), primary, yearly=True, actual_n=args.years)
    print_financials(info.get("financials"), primary, yearly=False, actual_n=args.quarters)
    print_eps_changes(info.get("eps_changes"))
    if args.summaries:
        if reports:
            print_reports(reports, args.summaries, args.detail_chars)
        else:
            print(f"[리포트 요약] 미수집 — {errors.get('reports', '빈 응답')}")
    if news:
        print_news(news, args.since)

    for key, msg in errors.items():
        print(f"[누락] {key} — {msg}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
