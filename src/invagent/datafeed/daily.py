"""판정용 일봉 — StockEasy 정규장 일봉 우선, 네이버 대체.

네이버 `siseJson`은 장 마감 후 시간외가를 그날 종가로 보여 준다(2026-09-14부터 대덕전자 종가가
StockEasy와 매일 어긋났다: 9/14 95,600 vs 97,000원). 룰 발동은 정규장 종가 기준이므로
StockEasy `info-tab`의 `chart`(3년치 1일봉)를 정본으로 쓴다.
"""

from __future__ import annotations

from datetime import date, timedelta

from invagent.datafeed import cache, naver, stockeasy


def chart_bars(info: dict) -> list[dict]:
    """`info-tab` 응답의 `chart`를 네이버 일봉과 같은 모양의 리스트로 바꾼다."""
    chart = (info or {}).get("chart") or {}
    fields = (chart.get("schema") or {}).get("fields") or []
    bars = []
    for row in chart.get("data") or []:
        rec = dict(zip(fields, row))
        try:
            bars.append(
                {
                    "date": str(rec["timestamp"])[:10].replace("-", ""),
                    "open": float(rec["open"]),
                    "high": float(rec["high"]),
                    "low": float(rec["low"]),
                    "close": float(rec["close"]),
                    "volume": float(rec["volume"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    bars.sort(key=lambda b: b["date"])
    return bars


def fetch_daily_bars(
    code: str, days: int, asof: date | str | None = None
) -> tuple[list[dict], str | None, str | None]:
    """판정용 일봉을 받는다. `(bars, 오류, 대체 사유)`.

    대체 사유가 None이면 StockEasy 정규장 일봉이다.
    """
    end = naver.parse_date(asof, "asof") if asof is not None else date.today()
    start = (end - timedelta(days=days)).strftime("%Y%m%d")
    end_key = end.strftime("%Y%m%d")
    cookie = stockeasy.load_cookie()
    if not cookie:
        reason = f"{stockeasy.COOKIE_ENV} 미설정"
    else:
        # 정규장 봉만 쓰므로 15:40 뒤 받은 응답은 다음 개장까지 그대로 쓴다.
        info, err = stockeasy.fetch_stock_json(
            stockeasy.ENDPOINTS["info_tab"].format(code=code),
            cookie=cookie,
            closed_from=cache.REGULAR_SESSION_SETTLED,
        )
        bars = [b for b in chart_bars(info) if start <= b["date"] <= end_key]
        if bars:
            return bars, None, None
        reason = f"StockEasy 조회 실패 — {err}" if err else "StockEasy chart 일봉 없음"
    bars, err = naver.fetch_bars(code, days, asof=end)
    return bars, err, fallback_note(reason)


def fallback_note(reason: str) -> str:
    """네이버 대체 꼬리표. 스크립트는 이 문구를 그대로 출력에 붙인다."""
    return f"네이버 일봉 대체 ({reason}) — 장 마감 후 시간외가가 종가에 섞였을 수 있다"
