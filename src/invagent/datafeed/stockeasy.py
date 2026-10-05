"""StockEasy JSON API 클라이언트.

종목 API(`/stockdata/api/v1/**`)와 시장 API(`.../market/**`)가 같은 호스트를 쓰고 인증 축도
같아 한 모듈에 둔다. 종목 페이지·리포트 페이지는 클라이언트 렌더링이라 페이지를 긁지 않고
페이지가 부르는 API를 그대로 부른다.

**`stock-search`를 뺀 모든 엔드포인트는 로그인 세션을 요구한다** (2026-08 기준으로 `info-tab`과
`news/by-stock-code`까지 401). 쿠키는 `STOCKEASY_COOKIE`에서 한 번 읽어 그대로 실어 보내고,
값은 어디에도 출력하지 않는다.
"""

from __future__ import annotations

import re
import urllib.parse

from invagent.datafeed import http
from invagent.datafeed.env import env_value

API_BASE = "https://stockeasy.intellio.kr/stockdata/api/v1"
PAGE_BASE = "https://stockeasy.intellio.kr/stock-analysis/stock-info"
REPORTS_PAGE = "https://stockeasy.intellio.kr/stock-analysis/reports"
ENDPOINTS = {
    "search": "/stock-search/",
    "info_tab": "/stock-info/info-tab/{code}",
    # 2026-09 개편 페이지의 소식 탭 — 공시·리포트·뉴스 각 최근 20건과 목표주가 이력.
    "analysis_tab": "/stock-info/analysis-tab/{code}",
    "news": "/news/by-stock-code/{code}",
    "reports": "/securities-reports",
}

MARKET_API_BASE = "https://stockeasy.intellio.kr/stockdata/api/v1/market"
MARKET_PAGE_URL = "https://stockeasy.intellio.kr/market-analysis?tab=overview"
MARKET_ENDPOINTS = {
    "indices": "/indices",
    "big_picture": "/big-picture",
    "market_monitor": "/market-monitor",
    "credit_balance": "/credit-balance",
}

TIMEOUT = http.TIMEOUT
TICKER_RE = re.compile(r"^\d{6}$")
COOKIE_ENV = "STOCKEASY_COOKIE"


def load_cookie() -> str | None:
    """`STOCKEASY_COOKIE`를 환경변수 → 저장소 `.env` 순으로 찾는다.

    값(쿠키 본문)은 출력하지 않는다 — 로그·보고서로 새면 안 된다.
    """
    return env_value(COOKIE_ENV)


def fetch_stock_json(
    path: str,
    params: dict | None = None,
    referer: str = PAGE_BASE,
    cookie: str | None = None,
    closed_from=None,
):
    """종목 API 하나를 호출해 JSON을 반환한다. 실패하면 `(None, 사유)`."""
    url = API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return http.get_json(
        url,
        authed=bool(cookie),
        headers=http.build_headers(referer=referer, cookie=cookie),
        timeout=TIMEOUT,
        closed_from=closed_from,
    )


def fetch_market_json(name: str):
    """시장 API 하나를 호출해 JSON을 반환한다. 실패하면 `(None, 사유)`.

    무인증 엔드포인트라 캐시의 인증 축은 항상 anon이다.
    """
    url = MARKET_API_BASE + MARKET_ENDPOINTS[name]
    return http.get_json(
        url,
        authed=False,
        headers=http.build_headers(referer=MARKET_PAGE_URL),
        timeout=TIMEOUT,
    )


def resolve_stock(query: str):
    """종목명 또는 6자리 티커 → (종목 레코드, 사유, exit_code)."""
    if TICKER_RE.match(query):
        return {"stock_code": query, "stock_name": None, "exchange": None}, None, 0

    data, err = fetch_stock_json(ENDPOINTS["search"], {"q": query})
    if err:
        return None, f"종목 검색 실패 — {err}", 1
    hits = [h for h in (data or []) if h.get("market") == "KR"] or (data or [])
    return select_hit(hits, query)


def select_hit(hits: list[dict], query: str):
    """검색 결과 → (종목 레코드, 사유, exit_code). 이름이 정확히 같은 하나, 아니면 유일한 결과."""
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


def fs_rows(financials: dict, primary: str, yearly: bool) -> tuple[list, list]:
    """primary_fs_type(C/S)에 맞는 (확정, 추정) 리스트.

    `info-tab` 페이로드의 모양을 아는 함수라 수집 계층에 산다 — 예전에는 스킬 스크립트의
    private 심볼(`_fs_rows`)이었는데 다른 스킬이 그것을 넘어와 부르고 있었다.
    """
    prefix = "consolidated" if primary != "S" else "separate"
    if yearly:
        return (
            financials.get(f"{prefix}Yearly") or [],
            financials.get(f"{prefix}YearlyEstimate") or [],
        )
    return financials.get(prefix) or [], financials.get(f"{prefix}Estimate") or []
