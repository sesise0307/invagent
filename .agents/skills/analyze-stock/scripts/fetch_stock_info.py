#!/usr/bin/env python3
"""StockEasy 종목정보 수집기.

stockeasy.intellio.kr의 `/stockdata/api/v1/**` JSON API에서 개별 종목의 시세·멀티플·
증권사별 목표주가 추이·컨센서스 추정 실적·뉴스를 받아 압축 요약을 출력한다.

종목정보 페이지(`/stock-analysis/stock-info/<ticker>`)는 클라이언트 렌더링이라 HTML에
데이터가 없다. `fetch_market_signals.py`와 같은 방식으로 페이지가 실제 호출하는 API를 친다.

`info-tab` 원본 응답은 128KB 규모(3년치 차트 포함)라 그대로 읽으면 컨텍스트가 마른다.
차트 계열은 버리고 판단에 쓰는 값만 남긴다.

`stock-search`를 뺀 모든 엔드포인트가 로그인 세션을 요구한다. 브라우저에서 복사한 Cookie 헤더를
`.env`의 `STOCKEASY_COOKIE`에 넣는다. `info-tab`이 실패하면 exit 1, 리포트 요약·공시 목록만 실패하면
그 섹션만 비고 나머지는 정상 출력한다(비블로킹).

2026-09 개편 페이지 기준으로 `info-tab`은 KRX 시간외·NXT 체결가(`after_hours_quote`·`nxt_quote`)를
따로 싣고, 소식 탭(`analysis-tab`)은 종목별 DART 공시 목록을 싣는다. 시간외가는 참고줄로만 찍는다 —
판정 가격은 정규장 종가(`stock_info.cur_prc`)다.

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
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from math import isfinite
from pathlib import Path

from invagent.datafeed import cache as http_cache, ratelimit as datafeed_ratelimit, stockeasy, tickers

# 엔드포인트·자격증명 축은 수집 계층이 소유한다. 여기서는 출력문과 SKILL.md가 쓰는
# 이름만 그대로 다시 노출한다.
API_BASE = stockeasy.API_BASE
PAGE_BASE = stockeasy.PAGE_BASE
REPORTS_PAGE = stockeasy.REPORTS_PAGE
ENDPOINTS = stockeasy.ENDPOINTS
TIMEOUT = stockeasy.TIMEOUT
TICKER_RE = stockeasy.TICKER_RE
COOKIE_ENV = stockeasy.COOKIE_ENV

# 추정치는 컨센서스이지 공시가 아니다. 표 헤더에 그대로 박아 보고서로 흘려보낸다.
ESTIMATE_NOTE = "추정 — 컨센서스, DART 데이터 아님"


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


AFTER_HOURS_SESSIONS = {"pre": "프리마켓", "after": "애프터마켓"}


def print_after_hours(info: dict, regular_price: float | None) -> None:
    """KRX 시간외·NXT 체결가를 정규장가와 떼어 참고줄로 보여 준다.

    판정은 정규장 종가로만 한다. API 등락률은 전일 종가 대비라 오늘 정규장가 대비로 다시 잰다.
    """
    parts, stamp = [], None
    for label, quote in (
        ("KRX 시간외", info.get("after_hours_quote")),
        ("NXT", info.get("nxt_quote")),
    ):
        price = unsigned((quote or {}).get("cur_prc"))
        if not price:
            continue
        if label == "NXT":
            session = quote.get("session")
            label += " " + AFTER_HOURS_SESSIONS.get(session, session or "")
        gap = (
            f"(정규장가 대비 {(price / regular_price - 1) * 100:+.1f}%)" if regular_price else ""
        )
        parts.append(f"{label.strip()} {price:,.0f}원{gap}")
        tm = str(quote.get("cntr_tm") or "")
        stamp = stamp or f"{ymd(quote.get('dt'))}" + (
            f" {tm[:2]}:{tm[2:4]}:{tm[4:6]}" if len(tm) == 6 else ""
        )
    if parts:
        print(f"[시간외·NXT] {' · '.join(parts)} — {stamp} · 참고용, 판정 미사용(정규장 종가 기준)")


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


def numeric_target(value) -> float | None:
    """유효한 양의 목표가만 반환한다. NR·문자열 상태·NaN은 제외한다."""
    if isinstance(value, bool) or value in (None, "", "-"):
        return None
    try:
        target = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return target if isfinite(target) and target > 0 else None


def build_consensus_summary(history: list, current_price: float | None = None) -> dict:
    """증권사별 최신 보고서를 먼저 고른 뒤 숫자 목표가만 집계한다.

    최신이 NR이면 같은 증권사의 옛 목표가를 되살리지 않는다. 제외된 최신 보고서도
    날짜와 상태를 남겨 valuation 자동화가 컨센서스 범위를 재사용할 수 있게 한다.
    """
    rows = sorted(history or [], key=lambda r: r.get("report_date") or "", reverse=True)
    latest: dict[str, dict] = {}
    for row in rows:
        broker = str(row.get("securities_company") or "미상")
        latest.setdefault(broker, row)

    included: list[dict] = []
    excluded: list[dict] = []
    for broker, row in latest.items():
        target = numeric_target(row.get("target_price"))
        if target is None:
            excluded.append(
                {
                    "broker": broker,
                    "report_date": row.get("report_date"),
                    "opinion": row.get("investment_opinion"),
                    "target_status": str(row.get("target_price") or "NR/미제시"),
                }
            )
            continue
        included.append(
            {
                "broker": broker,
                "report_date": row.get("report_date"),
                "target_price": target,
                "target_price_change": row.get("target_price_change"),
            }
        )

    targets = [row["target_price"] for row in included]
    summary = {
        "method": "latest_report_per_broker_then_numeric_target",
        "coverage_brokers": len(latest),
        "included_brokers": len(included),
        "included": included,
        "excluded_latest": excluded,
        "average": sum(targets) / len(targets) if targets else None,
        "minimum": min(targets) if targets else None,
        "maximum": max(targets) if targets else None,
        "upgrades": sum(1 for row in included if row.get("target_price_change") == "상향"),
        "downgrades": sum(1 for row in included if row.get("target_price_change") == "하향"),
    }
    if current_price and summary["average"] is not None:
        summary["upside_percent"] = (summary["average"] - current_price) / current_price * 100
    else:
        summary["upside_percent"] = None
    return summary


def print_target_prices(history: list, current_price: float | None, limit: int, since: str | None) -> None:
    rows = sorted(history or [], key=lambda r: r.get("report_date") or "", reverse=True)
    print(f"[증권사별 목표주가] 총 {len(rows)}건" + (f" (최근 {limit}건 표시)" if len(rows) > limit else ""))
    if not rows:
        print("  (없음)")
        return
    print("  | 발간일 | 증권사 | 애널리스트 | 의견 | 목표가 | 변동 | 당시주가 | 괴리율 | 리포트 |")
    print("  |---|---|---|---|---|---|---|---|---|")
    for r in rows[:limit]:
        tp = numeric_target(r.get("target_price"))
        cp = numeric_target(r.get("current_price"))
        up = r.get("upside_potential")
        new_mark = " 🆕" if since and (r.get("report_date") or "") > since else ""
        print(
            f"  | {r.get('report_date') or '-'}{new_mark} | {r.get('securities_company') or '-'} | "
            f"{r.get('author') or '-'} | {r.get('investment_opinion') or '-'} | "
            f"{format(tp, ',.0f') + '원' if tp else '-'} | {r.get('target_price_change') or '-'} | "
            f"{format(cp, ',.0f') + '원' if cp else '-'} | "
            f"{format(up, '+.1f') + '%' if up is not None else '-'} | {r.get('title') or '-'} |"
        )

    consensus = build_consensus_summary(rows, current_price)
    current = consensus["included"]
    if not current:
        print("[컨센 요약] 목표가 제시 리포트 없음 (전부 Not Rated)")
        for row in consensus["excluded_latest"]:
            print(
                f"  ↳ 제외: {row['broker']} 최신 {row['report_date'] or '-'} · "
                f"{row['opinion'] or '의견 없음'} · {row['target_status']}"
            )
        return
    avg = consensus["average"]
    upside = (
        f" · 현재가 대비 {consensus['upside_percent']:+.1f}%"
        if consensus["upside_percent"] is not None
        else ""
    )
    print(
        f"[컨센 요약] 평균 목표가 {avg:,.0f}원 "
        f"(최고 {consensus['maximum']:,.0f} / 최저 {consensus['minimum']:,.0f}) · "
        f"커버 {consensus['included_brokers']}사 / 목표가 제시 {len([numeric_target(row.get('target_price')) for row in rows if numeric_target(row.get('target_price')) is not None])}건 · "
        f"최신 보고서 {consensus['coverage_brokers']}사 중 유효 목표가 "
        f"{consensus['included_brokers']}사 · 상향 {consensus['upgrades']} / "
        f"하향 {consensus['downgrades']}{upside}"
    )
    for row in consensus["excluded_latest"]:
        print(
            f"  ↳ 제외: {row['broker']} 최신 {row['report_date'] or '-'} · "
            f"{row['opinion'] or '의견 없음'} · {row['target_status']}"
        )
    rated = [numeric_target(row.get("target_price")) for row in rows]
    rated = [target for target in rated if target is not None]
    if len(rated) > len(current):
        hist = rated
        hist_avg = sum(hist) / len(hist)
        print(
            f"  ↳ 기준 = 증권사별 최신 1건. 전체 이력 {len(rated)}건 단순평균은 "
            f"{hist_avg:,.0f}원 (최고 {max(hist):,.0f} / 최저 {min(hist):,.0f}) — "
            f"옛 목표가·중복 포함이라 9단계 blend 입력이 아니다"
        )


def _period(row: dict, yearly: bool) -> str:
    if yearly:
        return f"{row.get('year')}"
    return f"{row.get('year')}.{row.get('quarter')}Q"


def print_financials(financials: dict | None, primary: str, yearly: bool, actual_n: int) -> None:
    if not financials:
        return
    actual, estimate = stockeasy.fs_rows(financials, primary, yearly)
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
        tp = numeric_target(r.get("target_price"))
        head = (
            f"  - {r.get('report_date') or '-'} [{r.get('securities_company') or '-'}] "
            f"{r.get('author') or '-'} · {r.get('investment_opinion') or '의견 없음'}"
        )
        if tp:
            head += f" 목표가 {tp:,.0f}원"
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
    all_items = (news or {}).get("items") or []
    items = all_items
    if since:
        items = [n for n in items if (n.get("published_at") or "")[:10] >= since]
    if since:
        print(f"[뉴스] {len(items)}건 (기준일 {since} 이후) · API 최근 {len(all_items)}건 중 필터")
    else:
        print(f"[뉴스] API 최근 {len(items)}건")
    for n in items:
        published = (n.get("published_at") or "")[:10] or "-"
        print(f"  - {published} [{n.get('source') or '-'}] {n.get('title')}")
        print(f"    {n.get('link') or n.get('original_link') or '-'}")


DART_VIEWER = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="


def print_disclosures(analysis: dict, since: str | None, limit: int) -> None:
    """페이지 소식 탭(`analysis-tab`)의 DART 공시 목록. 본문 판독은 opendart 스킬의 몫이다."""
    all_items = (analysis or {}).get("disclosures") or []
    items = all_items
    if since:
        items = [d for d in items if (d.get("rcept_dt") or "")[:10] >= since]
        print(f"[공시] {min(len(items), limit)}건 (기준일 {since} 이후) · API 최근 {len(all_items)}건 중 필터")
    else:
        print(f"[공시] API 최근 {min(len(items), limit)}건")
    for d in items[:limit]:
        rcept_no = d.get("rcept_no") or "-"
        print(f"  - {d.get('rcept_dt') or '-'} {_clean(d.get('report_nm'))} · rcept_no {rcept_no}")
        if d.get("rcept_no"):
            print(f"    {DART_VIEWER}{rcept_no}")


# --- main ------------------------------------------------------------------


def news_coverage(news: dict | None, requested_limit: int, since: str | None) -> dict:
    """지원이 확인되지 않은 페이지네이션을 가정하지 않고 조회 범위를 표시한다."""
    items = (news or {}).get("items") or []
    return {
        "scope": "latest_n_items",
        "requested_limit": requested_limit,
        "returned_count": len(items),
        "since": since,
        # since 증분은 최근 N건 창 밖에 빠진 기사가 없는지 증명할 수 없다.
        "complete": since is None,
        "label": (
            f"API 최근 {len(items)}건 중 {since} 이후"
            if since
            else f"API 최근 {len(items)}건"
        ),
    }


def build_summary(
    stock: dict,
    info: dict | None,
    news: dict | None,
    reports: dict | None = None,
    *,
    news_limit: int = 10,
    since: str | None = None,
    own_info: dict | None = None,
    analysis: dict | None = None,
) -> dict:
    """--json 출력용 압축 dict. 차트 계열은 버린다.

    우선주면 `info`는 **본주** 페이로드(실적·컨센·목표주가)이고 `own_info`가 우선주 자기
    페이로드(시세·52주·수급)다. 둘은 가격이 다르므로 섞지 않고 축별로 갈라 담는다.
    """
    info = info or {}
    quote = (own_info or info).get("stock_info")
    parent_prc = unsigned((info.get("stock_info") or {}).get("cur_prc"))
    own_prc = unsigned((quote or {}).get("cur_prc")) if own_info else None
    return {
        "stock_code": stock.get("stock_code"),
        "stock_name": stock.get("stock_name"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "preferred_of": tickers.common_code(stock.get("stock_code") or ""),
        "preferred_discount_pct": (
            (own_prc / parent_prc - 1) * 100 if own_prc and parent_prc else None
        ),
        "stock_info": quote,
        # 참고용 — 판정은 정규장 종가(`stock_info.cur_prc`)로만 한다.
        "after_hours_quote": (own_info or info).get("after_hours_quote"),
        "nxt_quote": (own_info or info).get("nxt_quote"),
        "primary_fs_type": info.get("primary_fs_type"),
        "sector_info": info.get("sector_info"),
        "rs_data": info.get("rs_data"),
        "investment": info.get("investment"),
        "target_price_history": info.get("target_price_history"),
        # 목표주가는 본주에 매겨진다 — 우선주 주가에 대면 괴리율만큼 상승여력이 부풀려진다.
        "consensus": build_consensus_summary(
            info.get("target_price_history") or [], parent_prc
        ),
        "financials": info.get("financials"),
        "eps_changes": info.get("eps_changes"),
        "news": (news or {}).get("items"),
        "news_coverage": news_coverage(news, news_limit, since),
        "reports": (reports or {}).get("items"),
        "disclosures": (analysis or {}).get("disclosures"),
    }


def collect_stock_payloads(
    ticker: str,
    *,
    news_limit: int,
    summaries: int,
    since: str | None,
    cookie: str | None,
    disclosures: int = 0,
) -> tuple[dict | None, dict | None, dict | None, dict | None, dict | None, dict[str, str]]:
    """식별이 끝난 종목의 독립 API를 동시에 수집한다.

    우선주는 실적이 따로 없고 리포트·뉴스도 본주 이름으로 나온다. 그래서 `info_tab`·`news`·
    `reports`는 **본주 코드**로 부르고, 시세·52주·수급용으로 우선주 자기 `info_tab`을 하나 더
    부른다(`own_info`). 본주가 아니면 그 호출은 아예 만들지 않는다. 공시(`analysis`)도 회사
    단위라 본주 코드로 부른다.
    """
    fs_ticker, is_preferred = tickers.fundamentals_code(ticker)
    referer = f"{PAGE_BASE}/{fs_ticker}"
    jobs: dict[str, tuple[str, dict | None, str]] = {
        "info_tab": (ENDPOINTS["info_tab"].format(code=fs_ticker), None, referer),
    }
    if is_preferred:
        jobs["own_info"] = (
            ENDPOINTS["info_tab"].format(code=ticker),
            None,
            f"{PAGE_BASE}/{ticker}",
        )
    if news_limit:
        jobs["news"] = (ENDPOINTS["news"].format(code=fs_ticker), {"limit": news_limit}, referer)
    if disclosures:
        jobs["analysis"] = (ENDPOINTS["analysis_tab"].format(code=fs_ticker), None, referer)
    errors: dict[str, str] = {}
    if summaries:
        if cookie:
            params = {"stock_code": fs_ticker, "page": 1, "page_size": max(summaries, 10)}
            if since:
                params["date_from"] = since
            jobs["reports"] = (ENDPOINTS["reports"], params, REPORTS_PAGE)
        else:
            errors["reports"] = f"{COOKIE_ENV} 미설정 — .env에 브라우저 Cookie 헤더를 넣어야 한다"

    payloads: dict[str, dict | None] = {
        "info_tab": None, "news": None, "reports": None, "own_info": None, "analysis": None
    }

    def fetch_job(job: tuple[str, dict | None, str]):
        path, params, job_referer = job
        return stockeasy.fetch_stock_json(path, params, referer=job_referer, cookie=cookie)

    with ThreadPoolExecutor(max_workers=min(datafeed_ratelimit.MAX_CONCURRENT_PER_HOST, len(jobs))) as pool:
        futures = {name: pool.submit(fetch_job, job) for name, job in jobs.items()}
        for name, future in futures.items():
            payload, err = future.result()
            payloads[name] = payload
            if err:
                if name == "reports" and err == "HTTP 401":
                    err += " (쿠키 만료·무효 — .env 갱신 필요)"
                errors[name] = err
    return (
        payloads["info_tab"],
        payloads["news"],
        payloads["reports"],
        payloads["own_info"],
        payloads["analysis"],
        errors,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="StockEasy 종목정보 수집")
    parser.add_argument("query", help="종목명 또는 6자리 티커")
    parser.add_argument("--no-cache", action="store_true", help="캐시를 쓰지 않고 매번 새로 받는다 (캐시 오염 의심 시)")
    parser.add_argument("--news", type=int, default=10, help="뉴스 건수 (기본 10)")
    parser.add_argument("--reports", type=int, default=12, help="목표주가 표시 건수 (기본 12)")
    parser.add_argument("--quarters", type=int, default=4, help="확정 분기 표시 개수 (기본 4)")
    parser.add_argument("--years", type=int, default=3, help="확정 연도 표시 개수 (기본 3)")
    parser.add_argument("--since", help="YYYY-MM-DD. 뉴스·리포트는 이 날짜 이후만, 신규 리포트는 🆕 표시")
    parser.add_argument(
        "--summaries", type=int, default=5, help="리포트 요약 건수 (기본 5, 0이면 생략)"
    )
    parser.add_argument(
        "--disclosures", type=int, default=10, help="DART 공시 목록 건수 (기본 10, 0이면 생략)"
    )
    parser.add_argument(
        "--detail-chars",
        type=int,
        default=0,
        help="리포트 본문(detail_content)을 N자까지 함께 출력 (기본 0=요약만)",
    )
    parser.add_argument("--json", action="store_true", help="압축 JSON 덤프")
    args = parser.parse_args(argv)
    if args.no_cache:
        http_cache.disable()

    stock, err, code = stockeasy.resolve_stock(args.query)
    if err:
        print(f"ERROR: {err}", file=sys.stderr)
        return code

    ticker = stock["stock_code"]
    # 2026-08 이후 info-tab·news도 로그인 세션을 요구한다(비인증 호출은 HTTP 401).
    # 쿠키는 한 번만 읽어 세 엔드포인트에 함께 넘긴다. 없으면 종전대로 비인증으로 시도한다.
    cookie = stockeasy.load_cookie()

    info, news, reports, own_info, analysis, errors = collect_stock_payloads(
        ticker,
        news_limit=args.news,
        summaries=args.summaries,
        since=args.since,
        cookie=cookie,
        disclosures=args.disclosures,
    )

    if not info:
        detail = ", ".join(f"{k}={v}" for k, v in errors.items()) or "빈 응답"
        if errors.get("info_tab") == "HTTP 401":
            detail += (
                f" — 로그인 세션 필요. {COOKIE_ENV} "
                + ("만료·무효" if cookie else "미설정")
                + " (.env 갱신)"
            )
        print(f"ERROR: StockEasy 종목정보 호출 실패 — {detail}", file=sys.stderr)
        return 1

    # 우선주면 `info`는 본주 페이로드다 — 시세·52주·수급만 우선주 자기 것(`own_info`)을 쓴다.
    parent_si = info.get("stock_info") or {}
    si = (own_info or info).get("stock_info") or {}
    parent_code, is_preferred = tickers.fundamentals_code(ticker)
    name = stock.get("stock_name") or si.get("name") or ticker

    if args.json:
        print(
            json.dumps(
                build_summary(
                    {**stock, "stock_name": name},
                    info,
                    news,
                    reports,
                    news_limit=args.news,
                    since=args.since,
                    own_info=own_info,
                    analysis=analysis,
                ),
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
    if is_preferred:
        own_prc, parent_prc = unsigned(si.get("cur_prc")), unsigned(parent_si.get("cur_prc"))
        gap = f" · 괴리율 {(own_prc / parent_prc - 1) * 100:+.1f}%" if own_prc and parent_prc else ""
        print(
            f"[우선주] 시세·52주·수급은 {ticker} 자기 것 · 멀티플·컨센서스·목표주가·재무·뉴스·"
            f"리포트는 본주 {parent_si.get('name') or ''}({parent_code}) 기준{gap}"
        )
    print_quote(si)
    print_after_hours(own_info or info, unsigned(si.get("cur_prc")))
    print_multiples(parent_si if is_preferred else si)
    print_52w(si)
    print_flow(si)
    print_sector(info.get("sector_info"), info.get("rs_data"))
    print_grades(info.get("investment"))
    # 목표주가는 본주에 매겨진다 — 우선주 주가에 대면 상승여력이 괴리율만큼 부풀려진다.
    print_target_prices(
        info.get("target_price_history") or [],
        unsigned(parent_si.get("cur_prc")),
        args.reports,
        args.since,
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
    if args.disclosures:
        if analysis:
            print_disclosures(analysis, args.since, args.disclosures)
        else:
            print(f"[공시] 미수집 — {errors.get('analysis', '빈 응답')}")
    if news:
        print_news(news, args.since)

    for key, msg in errors.items():
        print(f"[누락] {key} — {msg}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
