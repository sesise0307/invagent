"""판정용 일봉 — 금융위 KRX 공식 일봉 우선, 빈 최근 봉은 StockEasy, 마지막 대체는 네이버.

네이버 `siseJson`은 장 마감 후 시간외가를 그날 종가로 보여 준다(2026-09-14부터 대덕전자 종가가
StockEasy와 매일 어긋났다: 9/14 95,600 vs 97,000원). 룰 발동은 정규장 종가 기준이다.

정본은 금융위원회 주식시세정보(`fsc`)다 — KRX 공식 종가이고 요청 한도가 넉넉하다. 다만 다음
영업일 13시 뒤에 올라오므로 그 사이의 봉만 StockEasy `info-tab`의 `chart`로 잇는다. 그래서
StockEasy는 장중·장 마감 직후에만 불린다 — 종목마다 3년치를 받던 때에는 연속 스캔이 열몇 번째
요청에서 끊겼다(2026-10-05·06). 공식 일봉이 없으면(키 없음·실패) StockEasy 3년치 → 네이버 순이다.
"""

from __future__ import annotations

from datetime import date, timedelta

from invagent.datafeed import cache, fsc, naver, stockeasy


OFFICIAL = "fsc"
STOCKEASY = "stockeasy"
NAVER = "naver"
SOURCE_KEY = "source"


def tagged(bars: list[dict], source: str) -> list[dict]:
    """봉마다 어느 소스에서 왔는지 적는다. `source_label`이 이것으로 출처를 밝힌다."""
    return [{**b, SOURCE_KEY: source} for b in bars]


def source_label(bars: list[dict], note: str | None) -> str:
    """스크립트 출력용 출처 표기. 실제로 쓴 소스를 구간과 함께 적는다.

    출처 태그가 없는 봉(테스트 대역 등)은 예전 표기 `StockEasy 일봉`을 쓴다.
    """
    official = [b for b in bars if b.get(SOURCE_KEY) == OFFICIAL]
    tail = [b for b in bars if b.get(SOURCE_KEY) == STOCKEASY]
    if not official:
        return note or "StockEasy 일봉"
    parts = [f"금융위 KRX 공식 일봉 ~{official[-1]['date']}"]
    if note:
        parts.append(note)
    elif tail:
        dates = ", ".join(b["date"] for b in tail)
        parts.append(f"StockEasy 정규장 {dates}")
    return " + ".join(parts)


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

    대체 사유가 None이면 전부 정규장 일봉(금융위 공식 + StockEasy)이다. 공식 일봉 뒤의 빈 봉을
    네이버로 채웠으면 그 날짜를 대체 사유에 적는다.
    """
    end = naver.parse_date(asof, "asof") if asof is not None else date.today()
    start = (end - timedelta(days=days)).strftime("%Y%m%d")
    end_key = end.strftime("%Y%m%d")
    official, _ = fsc.fetch_bars(code, days, asof=end)
    official = tagged(official, OFFICIAL)
    if official:
        last = official[-1]["date"]
        if not missing_weekdays(last, end):
            return official, None, None
        recent, reason = stockeasy_bars(code, end_key)
        recent = tagged([b for b in recent if b["date"] > last], STOCKEASY)
        if recent:
            return official + recent, None, None
        naver_bars, _ = naver.fetch_bars(code, days, asof=end)
        recent = tagged([b for b in naver_bars if b["date"] > last], NAVER)
        if recent:
            dates = ", ".join(b["date"] for b in recent)
            return official + recent, None, fallback_note(f"최근 봉 {dates} — {reason or 'StockEasy 일봉 없음'}")
        # 빈 날이 공휴일이었을 수 있다 — 공식 일봉만으로 판정한다.
        return official, None, None
    bars, reason = stockeasy_bars(code, end_key)
    bars = tagged([b for b in bars if b["date"] >= start], STOCKEASY)
    if bars:
        return bars, None, None
    bars, err = naver.fetch_bars(code, days, asof=end)
    return tagged(bars, NAVER), err, fallback_note(reason)


def stockeasy_bars(code: str, end_key: str) -> tuple[list[dict], str | None]:
    """StockEasy `info-tab` chart의 `end_key`까지 정규장 일봉. 없으면 ([], 사유)."""
    cookie = stockeasy.load_cookie()
    if not cookie:
        return [], f"{stockeasy.COOKIE_ENV} 미설정"
    # 정규장 봉만 쓰므로 15:40 뒤 받은 응답은 다음 개장까지 그대로 쓴다.
    info, err = stockeasy.fetch_stock_json(
        stockeasy.ENDPOINTS["info_tab"].format(code=code),
        cookie=cookie,
        closed_from=cache.REGULAR_SESSION_SETTLED,
    )
    bars = [b for b in chart_bars(info) if b["date"] <= end_key]
    if bars:
        return bars, None
    return [], f"StockEasy 조회 실패 — {err}" if err else "StockEasy chart 일봉 없음"


def missing_weekdays(last: str, end: date) -> bool:
    """`last` 다음 날부터 `end`까지 평일이 있는가. 공휴일은 모른다 — 그날은 한 번 더 물을 뿐이다."""
    day = naver.parse_date(last, "last") + timedelta(days=1)
    while day <= end:
        if day.weekday() < 5:
            return True
        day += timedelta(days=1)
    return False


def fallback_note(reason: str) -> str:
    """네이버 대체 꼬리표. 스크립트는 이 문구를 그대로 출력에 붙인다."""
    return f"네이버 일봉 대체 ({reason}) — 장 마감 후 시간외가가 종가에 섞였을 수 있다"
