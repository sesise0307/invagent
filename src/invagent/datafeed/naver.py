"""네이버 금융 일봉과 투자자별 매매동향 수집.

`api.finance.naver.com/siseJson.naver`는 무인증이고, 응답이 표준 JSON이 아니라 작은따옴표를
쓴 파이썬 리터럴이다. 투자자별 매매동향(`m.stock.naver.com/api/stock/<code>/trend`)도 무인증
JSON이고 한 번에 최근 60거래일까지 준다 — StockEasy에는 종목 기관 수급이 없어 이쪽이 출처다.
인증 축이 없으므로 캐시 키는 항상 anon이다.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from invagent.datafeed import http

SISE_URL = "https://api.finance.naver.com/siseJson.naver"
TREND_URL = "https://m.stock.naver.com/api/stock/{code}/trend"
TREND_MAX_DAYS = 60
REFERER = "https://finance.naver.com/"
TIMEOUT = http.TIMEOUT


def parse_sise(text: str) -> list[dict]:
    """`siseJson` 응답을 일봉 리스트로 바꾼다.

    헤더 행을 버리고 날짜 오름차순 리스트를 돌려준다. 행 하나가 깨져도 그 행만 버린다.
    """
    raw = json.loads(text.replace("'", '"'))
    bars = []
    for row in raw:
        if not row or str(row[0]).strip() in ("날짜", ""):
            continue
        try:
            bars.append(
                {
                    "date": str(row[0]),
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]),
                }
            )
        except (IndexError, TypeError, ValueError):
            continue
    bars.sort(key=lambda b: b["date"])
    return bars


def parse_date(value: date | str, field: str = "date") -> date:
    """기존 일봉 페이로드가 쓰는 ISO 또는 압축 YYYYMMDD 날짜를 받는다."""
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a date")
    try:
        return (
            date.fromisoformat(value)
            if "-" in value
            else datetime.strptime(value, "%Y%m%d").date()
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be YYYY-MM-DD or YYYYMMDD") from exc


def fetch_bars(code: str, days: int, asof: date | str | None = None) -> tuple[list[dict], str | None]:
    """네이버 일봉을 받아온다. 실패하면 ([], 사유).

    `peak_drawdown`은 보유 종목마다, `stage_scan`은 종목마다 이 함수를 부른다 —
    같은 창을 반복해서 받지 않도록 공용 캐시를 탄다.
    """
    end = parse_date(asof, "asof") if asof is not None else date.today()
    start = end - timedelta(days=days)
    url = (
        f"{SISE_URL}?symbol={code}&requestType=1"
        f"&startTime={start.strftime('%Y%m%d')}&endTime={end.strftime('%Y%m%d')}&timeframe=day"
    )
    bars, err = http.get_json(
        url,
        authed=False,
        headers=http.build_headers(referer=REFERER, accept="*/*"),
        timeout=TIMEOUT,
        decode=parse_sise,
    )
    return bars or [], err


def _signed_int(value) -> int:
    return int(str(value).replace(",", "").replace("+", "") or 0)


def parse_trend(text: str) -> list[dict]:
    """투자자별 매매동향 응답을 날짜 오름차순 순매수량 리스트로 바꾼다. 깨진 행은 버린다."""
    rows = []
    for row in json.loads(text):
        try:
            rows.append(
                {
                    "date": str(row["bizdate"]),
                    "institution": _signed_int(row["organPureBuyQuant"]),
                    "foreign": _signed_int(row["foreignerPureBuyQuant"]),
                    "individual": _signed_int(row["individualPureBuyQuant"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    rows.sort(key=lambda r: r["date"])
    return rows


def fetch_investor_trend(code: str, days: int = TREND_MAX_DAYS) -> tuple[list[dict], str | None]:
    """최근 `days`거래일(최대 60)의 기관·외국인·개인 순매수량(주). 실패하면 ([], 사유)."""
    url = f"{TREND_URL.format(code=code)}?pageSize={min(days, TREND_MAX_DAYS)}"
    rows, err = http.get_json(
        url,
        authed=False,
        headers=http.build_headers(referer="https://m.stock.naver.com/"),
        timeout=TIMEOUT,
        decode=parse_trend,
    )
    return rows or [], err
