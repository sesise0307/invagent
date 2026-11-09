"""autoanalyst 증권사 리포트 목록 클라이언트.

`api.autoanalyst.ai.kr`는 www.autoanalyst.ai.kr 페이지가 부르는 무인증 JSON API다. 이 모듈은
그중 종목별 국내 기업 리포트 목록(`report_list_get_by_recent`)만 받는다 — 증권사·목표가·의견·
목표가 변동·요약문이 한 행에 실린다. 일봉·멀티플·수급은 여기 없으므로 StockEasy `info-tab`이
계속 정본이다.

행은 StockEasy `securities-reports`·`target_price_history`와 같은 키로 바꿔 돌려준다 — 소비자가
출처마다 다른 키를 읽지 않게 하려는 것이다. `source`에 출처를 남긴다.

2026-11-09 5종목 대조: 둘 다 있는 리포트의 목표가는 전부 일치했고, 커버리지는 서로 겹치지 않는
부분이 있어(이쪽에만 한국투자·BNK·흥국, StockEasy에만 SK증권·그로쓰리서치 상당수) 소비자가
StockEasy 목표가 이력과 합친다. 요약문은 최근 90일 리포트의 85~90%에만 있다.
"""

from __future__ import annotations

import urllib.parse

from invagent.datafeed import http

API_BASE = "https://api.autoanalyst.ai.kr"
PAGE_URL = "https://www.autoanalyst.ai.kr/"
REPORTS_PATH = "/report_list_get_by_recent"
SOURCE = "autoanalyst"
# 서버가 받는 페이지 크기 상한 (2026-11-09 `limit=200`을 100으로 줄여 응답).
PAGE_SIZE = 100
# 종목 하나에 이만큼만 읽는다 — SK하이닉스 7개월치가 172건이었다.
MAX_PAGES = 5
SUMMARY_SEPARATOR = "///"


def _report_row(raw: dict) -> dict:
    summary = raw.get("summerized_contents") or ""
    return {
        "report_date": raw.get("report_date"),
        "securities_company": raw.get("brokerage"),
        "title": raw.get("title"),
        "investment_opinion": raw.get("opinion_extracted"),
        "target_price": raw.get("manual_target_price"),
        "target_price_change": raw.get("manual_price_change_type"),
        "summary_points": [p.strip() for p in summary.split(SUMMARY_SEPARATOR) if p.strip()],
        "source": SOURCE,
    }


def fetch_reports(code: str) -> tuple[list[dict] | None, str | None]:
    """종목 하나의 국내 기업 리포트를 최신순으로 받는다. 실패하면 `(None, 사유)`."""
    rows: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        params = {"page": page, "limit": PAGE_SIZE, "type": "국내", "company_code": code}
        url = f"{API_BASE}{REPORTS_PATH}?{urllib.parse.urlencode(params)}"
        payload, err = http.get_json(
            url, authed=False, headers=http.build_headers(referer=PAGE_URL)
        )
        if err:
            return None, err
        data = (payload or {}).get("data") or []
        rows.extend(_report_row(raw) for raw in data)
        if not data or len(rows) >= int(payload.get("total_count") or 0):
            break
    return rows, None
