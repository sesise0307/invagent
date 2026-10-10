"""판정용 일봉 — 금융위 KRX 공식 일봉 우선, 빈 최근 봉은 StockEasy, 마지막 대체는 네이버.

네이버 `siseJson`은 장 마감 후 시간외가를 그날 종가로 보여 준다(2026-09-14부터 대덕전자 종가가
StockEasy와 매일 어긋났다: 9/14 95,600 vs 97,000원). 룰 발동은 정규장 종가 기준이다.

정본은 금융위원회 주식시세정보(`fsc`)다 — KRX 공식 종가이고 요청 한도가 넉넉하다. 다만 다음
영업일 13시 뒤에 올라오므로 그 사이의 봉만 StockEasy `info-tab`의 `chart`로 잇는다. 그래서
StockEasy는 장중·장 마감 직후에만 불린다 — 종목마다 3년치를 받던 때에는 연속 스캔이 열몇 번째
요청에서 끊겼다(2026-10-05·06). KST 오늘 봉을 15:40 전에 물으면 그 봉은 장중 현재가라 네이버에서
받고(`naver_intraday`, 출처 표기 「네이버 장중」) StockEasy는 지난 빈 봉에만 쓴다 — 13시 뒤 장중이면
StockEasy 호출이 없다. 네이버 지난 봉 종가에는 시간외가가 섞이므로 어제 봉은 네이버로 잇지 않는다.
공식 일봉이 없으면(키 없음·실패) StockEasy 3년치 → 네이버 순이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from invagent.datafeed import cache, fsc, naver, stockeasy


OFFICIAL = "fsc"
STOCKEASY = "stockeasy"
NAVER = "naver"
# 15:40 전 오늘 봉 — 대체가 아니라 아직 종가가 아닌 장중 현재가다.
NAVER_INTRADAY = "naver_intraday"
SOURCE_KEY = "source"


def tagged(bars: list[dict], source: str) -> list[dict]:
    """봉마다 어느 소스에서 왔는지 적는다. `Bars.label`이 이것으로 출처를 밝힌다."""
    return [{**b, SOURCE_KEY: source} for b in bars]


@dataclass(frozen=True)
class Bars:
    """판정 일봉. 봉(`rows`, 날짜 오름차순)과 함께 수집 오류·네이버 대체 사유·공식 일봉 실패를 싣는다.

    `fallback`이 None이면 전부 정규장 일봉이다. 봉이 비었는지는 `rows`로 직접 본다.
    """

    rows: list[dict] = field(default_factory=list)
    error: str | None = None
    fallback: str | None = None
    official_error: str | None = None

    @property
    def closes(self) -> list[float]:
        return [b["close"] for b in self.rows]

    def closing_high(self, window: int) -> tuple[str, float] | None:
        """최근 `window`개 봉 중 종가가 가장 높은 봉의 `(날짜, 종가)`. 같은 종가면 앞선 날. 봉이 없으면 None."""
        recent = self.rows[-window:]
        if not recent:
            return None
        best = max(recent, key=lambda b: b["close"])
        return best["date"], best["close"]

    @property
    def label(self) -> str:
        """스크립트 출력용 출처 표기. 실제로 쓴 소스를 구간과 함께 적는다.

        출처 태그가 없는 봉(테스트 대역 등)은 예전 표기 `StockEasy 일봉`을 쓴다. 키가 있는데 공식 일봉이
        실패했으면 그 사유를 앞에 붙인다 — 키가 없는 것은 실패가 아니라 적지 않는다.
        """
        prefix = f"공식 일봉 실패: {self.official_error} → " if self.official_error else ""
        return prefix + self._sources()

    def _sources(self) -> str:
        bars, note = self.rows, self.fallback
        official = [b for b in bars if b.get(SOURCE_KEY) == OFFICIAL]
        tail = [b for b in bars if b.get(SOURCE_KEY) == STOCKEASY]
        intraday = [b for b in bars if b.get(SOURCE_KEY) == NAVER_INTRADAY]
        if not official:
            return note or "StockEasy 일봉"
        parts = [f"금융위 KRX 공식 일봉 ~{official[-1]['date']}"]
        if note:
            parts.append(note)
        elif tail:
            dates = ", ".join(b["date"] for b in tail)
            parts.append(f"StockEasy 정규장 {dates}")
        if intraday:
            parts.append(f"네이버 장중 {', '.join(b['date'] for b in intraday)}")
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


def fetch_daily_bars(code: str, days: int, asof: date | str | None = None) -> Bars:
    """판정 일봉을 받는다.

    `fallback`이 None이면 전부 정규장 일봉(금융위 공식 + StockEasy)이다. 공식 일봉 뒤의 빈 봉을
    네이버로 채웠으면 그 날짜를 `fallback`에 적는다. 금융위 키가 없으면 금융위에 묻지 않는다.
    """
    end = naver.parse_date(asof, "asof") if asof is not None else date.today()
    start = (end - timedelta(days=days)).strftime("%Y%m%d")
    end_key = end.strftime("%Y%m%d")
    official: list[dict] = []
    official_error = None
    if fsc.load_key():
        official, official_error = fsc.fetch_bars(code, days, asof=end)
        official = tagged(official, OFFICIAL)
        if official:
            official_error = None
    if official:
        last = official[-1]["date"]
        if not missing_weekdays(last, end):
            return Bars(official)
        # 15:40 전 오늘 봉은 장중 현재가다 — 네이버로 잇고 StockEasy는 지난 봉에만 쓴다.
        today: list[dict] = []
        if settling_today(end):
            naver_bars, _ = naver.fetch_bars(code, days, asof=end)
            today = tagged([b for b in naver_bars if b["date"] == end_key], NAVER_INTRADAY)
            end -= timedelta(days=1)
            end_key = end.strftime("%Y%m%d")
            if not missing_weekdays(last, end):
                return Bars(official + today)
        recent, reason = stockeasy_bars(code, end_key)
        recent = tagged([b for b in recent if b["date"] > last], STOCKEASY)
        if recent:
            return Bars(official + recent + today)
        naver_bars, _ = naver.fetch_bars(code, days, asof=end)
        recent = tagged([b for b in naver_bars if b["date"] > last], NAVER)
        if recent:
            dates = ", ".join(b["date"] for b in recent)
            return Bars(
                official + recent + today,
                fallback=fallback_note(f"최근 봉 {dates} — {reason or 'StockEasy 일봉 없음'}"),
            )
        # 빈 날이 공휴일이었을 수 있다 — 공식 일봉만으로 판정한다.
        return Bars(official + today)
    bars, reason = stockeasy_bars(code, end_key)
    bars = tagged([b for b in bars if b["date"] >= start], STOCKEASY)
    if bars:
        return Bars(bars, official_error=official_error)
    bars, err = naver.fetch_bars(code, days, asof=end)
    return Bars(tagged(bars, NAVER), err, fallback_note(reason), official_error)


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


def settling_today(end: date) -> bool:
    """`end`가 KST 오늘이고 정규장 종가가 아직 확정 전(15:40 전)인가."""
    now = datetime.fromtimestamp(cache._now(), cache.KST)
    return end == now.date() and now.time() < cache.REGULAR_SESSION_SETTLED


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
