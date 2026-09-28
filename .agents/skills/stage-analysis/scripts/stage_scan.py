#!/usr/bin/env python3
"""Stage Analysis 단계 판정기 (통합 버전).

개별 종목이 주가 성숙 4단계 중 어디에 있는지 **결정론적으로** 판정한다.
근거는 DB증권 「Stage Analysis 마스터하기」(2026-08-25) III·IV장의 통합 버전이다.
스탠 와인스타인의 가격·이동평균 축에 마크 미너비니의 영업이익 증가율 축을 얹는다.

데이터 소스 둘:

1. 일봉 OHLCV — `invagent.datafeed.daily` (StockEasy 정규장 우선, 네이버 대체 시 꼬리표). 150일 이동평균·기울기·스윙 고저점·박스권.
2. 분기 영업이익 — StockEasy `info-tab`의 `financials` (로그인 쿠키 필요).
   쿠키가 없거나 만료되면 **가격 전용 판정(와인스타인 원본 버전)으로 강등**하고 그 사실을
   출력에 남긴다. 판정 자체는 계속되므로 종료 코드는 0이다.

티커 해석·쿠키 로딩·재무 행 선택은 `analyze-stock/scripts/fetch_stock_info.py`를 재사용한다.
같은 규칙을 두 곳에 두면 갈라진다.

의존성: stdlib만 사용 (urllib, json, argparse).
종료 코드: 0 정상(부분 누락 포함) / 1 시세 수집 실패 / 2 종목명 후보 다수.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from invagent.datafeed import cache as http_cache, naver, series, stockeasy, tickers
from invagent.datafeed import daily
from invagent.datafeed.naver import parse_sise
from invagent.datafeed.naver import parse_date as _parse_date
from invagent.datafeed.series import sma

# 출력문과 테스트가 쓰는 재노출.
SISE_URL = naver.SISE_URL
TIMEOUT = naver.TIMEOUT

# --- 판정 임계값 -----------------------------------------------------------
# 리포트는 "150일선 위/아래", "평탄", "저점·고점이 높아진다"를 말로만 규정하고 수치를 주지
# 않는다. 아래 값은 이 스킬이 정한 운영 기준이다. 바꾸려면 SKILL.md의 근거도 함께 고친다.
MA_DAYS = 150            # 통합 버전 기준선. 200일선은 참고로 병기만 한다 (리포트 p.17~18).
MA_REF_DAYS = 200        # 미너비니 원본 기준선 — 참고 표시용
MA_20WEEK = 100          # 기존 공개 상수: 일봉 근사 길이. 실제 주봉 함수는 WEEK_COUNT를 사용.
WEEK_COUNT = 20          # 「기술적 분석 규칙 1」의 실제 주봉 종가 표본 수
# 「매매규칙 12」의 장대 양봉 정의(2026-09-05 사용자 확정): 전일 종가 대비 +8% 이상.
# 정의는 `context/my_rules.md`가 정본이고 테스트가 원문에서 파싱해 이 상수와 대조한다.
LONG_BULL_PCT = 8.0
LONG_BULL_WINDOW = 20    # 진입가를 구속하는 최근 구간 (거래일). 반년 전 양봉은 오늘과 무관하다
SLOPE_WINDOW = 20        # 기울기 측정 구간 (거래일)
SLOPE_FLAT_PCT = 1.5     # ±1.5%/20일 이내면 "평탄"
POSITION_WINDOW = 20     # 주가-이평선 위치 판정 구간 (거래일)
POSITION_ABOVE = 0.8     # 최근 20일 중 80% 이상 위 → "위"
POSITION_BELOW = 0.2     # 20% 이하 → "아래", 그 사이는 "오르내림"
PIVOT_K = 10             # 스윙 피벗 프랙탈 반경 (좌우 거래일)
BOX_WINDOW = 60          # 박스권 상·하단 측정 구간 (거래일)
CYCLE_WINDOW = 250       # 순환적 고점·저점 탐색 구간 (거래일 ≈ 1년)
TREND_WINDOW = 250       # 중간 구간 1·3단계 가르기용 장기 방향 구간
PROJECT_HORIZONS = (20, 40, 60, 80, 100)   # 전망 계산 구간 (거래일)
PROJECT_MAX_DAYS = 400   # 전망 탐색 상한 (거래일)
TRADING_DAYS_PER_MONTH = 21

STAGE_LABEL = {
    1: "1단계 (기초 지역 / 무시 국면)",
    2: "2단계 (상승 국면 / 매집)",
    3: "3단계 (최정상 지역 / 분산)",
    4: "4단계 (쇠퇴 국면 / 투매)",
}

STAGE_ACTION = {
    1: "기다리며 지켜본다. 매수 대상이 아니다 — 「기본 원칙 8(풍림화산)」 대기 구간.",
    2: "매수 대상 구간 (경로 A, 추세 확인 진입). 1·3·4단계의 '바닥 다진 후 고개 드는 초반'은 "
       "turn_scan.py의 단기 바닥 전환 판정이 정본이다.",
    3: "신규 매수 금지. 「매매규칙 11」 수익 쿠션 확보 · 「매매규칙 5」 분할 매도 구간.",
    4: "접근에 신중해야 한다. 「매매규칙 6」 -15% 1차 분할 → -20% 전량 · 「매매규칙 8」 물타기 금지.",
}


# --- 시세 수집 -------------------------------------------------------------


def weekly_last_closes(bars: list[dict], asof: date | str | None = None) -> list[dict]:
    """Return each completed ISO week's last available close.

    The current ISO week is excluded Monday through Friday because a date-only
    request cannot prove that Friday's live bar is final.  On Saturday or Sunday
    that week is complete and its last available trading close is included.  A
    holiday week therefore uses Thursday or the latest earlier trading day.
    """
    cutoff = _parse_date(asof, "asof") if asof is not None else date.today()
    current_week = cutoff.isocalendar()[:2]
    include_current = cutoff.weekday() >= 5
    grouped: dict[tuple[int, int], tuple[date, float]] = {}
    for bar in bars:
        try:
            bar_date = _parse_date(bar["date"], "bar.date")
            close = float(bar["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if bar_date > cutoff:
            continue
        week = bar_date.isocalendar()[:2]
        if week == current_week and not include_current:
            continue
        previous = grouped.get(week)
        if previous is None or bar_date > previous[0]:
            grouped[week] = (bar_date, close)
    result = [
        {"date": bar_date.isoformat(), "close": close}
        for _, (bar_date, close) in sorted(grouped.items())
    ]
    # Unit callers may provide synthetic labels rather than calendar dates. Keep
    # deterministic five-session grouping for those inputs; live Naver bars use
    # the date-aware path above.
    if not result and len(bars) >= 5:
        result = [
            {"date": str(chunk[-1].get("date", i)), "close": float(chunk[-1]["close"])}
            for i in range(0, len(bars) - 4, 5)
            for chunk in [bars[i : i + 5]]
        ]
    return result


def weekly_sma(bars: list[dict], weeks: int = WEEK_COUNT,
               asof: date | str | None = None) -> dict:
    """Calculate a real weekly-close SMA and its one-week direction."""
    if weeks <= 0:
        raise ValueError("weeks must be positive")
    observations = weekly_last_closes(bars, asof)
    closes = [row["close"] for row in observations]
    value = sum(closes[-weeks:]) / weeks if len(closes) >= weeks else None
    previous = sum(closes[-weeks - 1:-1]) / weeks if len(closes) >= weeks + 1 else None
    slope_pct = None
    slope = "판정 불가"
    if value is not None and previous:
        slope_pct = (value / previous - 1) * 100
        if math.isclose(slope_pct, 0.0, abs_tol=1e-12):
            slope = "평탄"
        else:
            slope = "상승" if slope_pct > 0 else "하락"
    return {
        "value": value,
        "slope": slope,
        "slope_pct": slope_pct,
        "weeks": min(len(closes), weeks),
        "last_week_date": observations[-1]["date"] if observations else None,
        "asof": (_parse_date(asof, "asof") if asof is not None else date.today()).isoformat(),
    }


# --- 가격 지표 (순수 함수) --------------------------------------------------


def ma_slope(ma: list[float | None], window: int = SLOPE_WINDOW) -> tuple[str, float | None]:
    """이동평균 기울기 → ("상승"/"하락"/"평탄", 변화율 %)."""
    valid = [v for v in ma if v is not None]
    if len(valid) < window + 1 or not valid[-1 - window]:
        return "판정 불가", None
    change = (valid[-1] - valid[-1 - window]) / valid[-1 - window] * 100
    if change > SLOPE_FLAT_PCT:
        return "상승", change
    if change < -SLOPE_FLAT_PCT:
        return "하락", change
    return "평탄", change


def long_bull_days(bars: list[dict], window: int = LONG_BULL_WINDOW) -> list[dict]:
    """최근 구간의 장대 양봉(전일 종가 대비 +`LONG_BULL_PCT`% 이상) 목록.

    「매매규칙 12」는 장대 양봉 위에 진입가를 잡는 것을 막는다. 막으려면 그 날의 종가를
    알아야 하므로 날짜·등락률·종가를 함께 낸다.
    """
    out: list[dict] = []
    for prev, cur in zip(bars[-window - 1 :], bars[-window:]):
        if not prev["close"]:
            continue
        change = (cur["close"] / prev["close"] - 1) * 100
        if change >= LONG_BULL_PCT:
            out.append({"date": cur.get("date"), "change": change, "close": cur["close"]})
    return out


def price_vs_ma(
    closes: list[float], ma: list[float | None], window: int = POSITION_WINDOW
) -> tuple[str, float | None, int]:
    """최근 구간 중 종가가 이평선 위에 머문 비율 → ("위"/"아래"/"오르내림", 비율, 표본수)."""
    pairs = [(c, m) for c, m in zip(closes, ma) if m is not None][-window:]
    if not pairs:
        return "판정 불가", None, 0
    above = sum(1 for c, m in pairs if c > m)
    below = sum(1 for c, m in pairs if c < m)
    ratio = above / len(pairs)
    if ratio >= POSITION_ABOVE:
        return "위", ratio, len(pairs)
    if below / len(pairs) >= 1 - POSITION_BELOW:
        return "아래", ratio, len(pairs)
    return "오르내림", ratio, len(pairs)


def swing_pivots(bars: list[dict], k: int = PIVOT_K) -> tuple[list[dict], list[dict]]:
    """좌우 k봉 프랙탈 고점·저점 (종가). 최근 k봉은 확정되지 않아 제외된다.

    순환적 고점·저점은 경계 규칙에서 종가와 비교되고 `analyze-stock`의 종가 기준 추세
    이탈선이 되므로 장중 꼬리가 아니라 종가로 잡는다.
    """
    return series.swing_pivots(bars, k, high_key="close", low_key="close")


def swing_trend(pivot_highs: list[dict], pivot_lows: list[dict]) -> str:
    """최근 두 개의 스윙 고점·저점으로 계단 방향을 읽는다."""
    if len(pivot_highs) < 2 or len(pivot_lows) < 2:
        return "판정 불가"
    hh = pivot_highs[-1]["price"] > pivot_highs[-2]["price"]
    hl = pivot_lows[-1]["price"] > pivot_lows[-2]["price"]
    if hh and hl:
        return "높아짐"
    if (pivot_highs[-1]["price"] < pivot_highs[-2]["price"]
            and pivot_lows[-1]["price"] < pivot_lows[-2]["price"]):
        return "낮아짐"
    return "혼조"


def box_range(bars: list[dict], window: int = BOX_WINDOW) -> dict:
    """최근 구간의 박스권 상·하단(종가)과 폭."""
    recent = bars[-window:]
    top = max(b["close"] for b in recent)
    bottom = min(b["close"] for b in recent)
    return {
        "top": top,
        "bottom": bottom,
        "width_pct": (top - bottom) / bottom * 100 if bottom else None,
        "days": len(recent),
    }


def cyclical_levels(
    bars: list[dict], pivot_highs: list[dict], pivot_lows: list[dict], window: int = CYCLE_WINDOW
) -> dict:
    """직전 순환적 고점·저점 (리포트 p.20~21의 경계 규칙 입력값)."""
    floor_index = max(0, len(bars) - window)
    highs = [p for p in pivot_highs if p["index"] >= floor_index]
    lows = [p for p in pivot_lows if p["index"] >= floor_index]
    top = max(highs, key=lambda p: p["price"]) if highs else None
    bottom = min(lows, key=lambda p: p["price"]) if lows else None
    return {"high": top, "low": bottom}


def band_position(bars: list[dict], window: int = TREND_WINDOW) -> tuple[str, float | None]:
    """최근 1년 종가 밴드에서 현재가가 어디인가 → ("하단"/"중단"/"상단", 위치 비율)."""
    recent = bars[-window:]
    top = max(b["close"] for b in recent)
    bottom = min(b["close"] for b in recent)
    if top == bottom:
        return "중단", None
    ratio = (bars[-1]["close"] - bottom) / (top - bottom)
    if ratio <= 1 / 3:
        return "하단", ratio
    if ratio >= 2 / 3:
        return "상단", ratio
    return "중단", ratio


def volume_bias(bars: list[dict], window: int = BOX_WINDOW) -> float | None:
    """보조 지표. 상승일 평균 거래량 ÷ 하락일 평균 거래량.

    리포트 p.18: 거래량은 3·4단계에서 해석이 겹치고 일간 노이즈가 커 **보조로만** 쓴다.
    """
    ups, downs = [], []
    for prev, cur in zip(bars[-window - 1 : -1], bars[-window:]):
        (ups if cur["close"] >= prev["close"] else downs).append(cur["volume"])
    if not ups or not downs or not sum(downs):
        return None
    return (sum(ups) / len(ups)) / (sum(downs) / len(downs))


# --- 전망: 이동평균 교차 시점 (--project) -----------------------------------
#
# 이동평균은 새 종가가 들어오고 `window`일 전 종가가 빠지며 움직인다. 앞으로 빠져나갈 값은
# **이미 확정돼 있으므로**, 주가를 한 값으로 가정하면 이평선의 미래 경로가 결정된다.
# 4단계 종목이 언제 2단계에 닿을 수 있는지(혹은 2단계 종목이 언제 이평선에 닿는지)를
# 예측이 아니라 **산술**로 답하는 부분이다. 주가 가정은 어디까지나 가정이라는 점을 잊지 마라.


def dropout_schedule(
    closes: list[float], window: int = MA_DAYS, bucket: int = 20, buckets: int = 7
) -> list[dict]:
    """앞으로 이동평균에서 빠져나갈 과거 종가의 구간별 평균.

    빠지는 값이 현재가보다 높을수록 이평선은 저절로 내려온다 — 하락 압력이 풀리는 속도다.
    """
    pending = closes[-window:]
    out = []
    for i in range(buckets):
        seg = pending[i * bucket : (i + 1) * bucket]
        if not seg:
            break
        out.append(
            {"from_day": i * bucket, "to_day": i * bucket + len(seg), "avg": sum(seg) / len(seg)}
        )
    return out


def project_ma_path(
    closes: list[float], ma_now: float, price: float, days: int, window: int = MA_DAYS
) -> list[float]:
    """주가가 `price`로 고정된다고 가정한 이동평균의 미래 경로 (오늘 값 포함)."""
    pending = list(closes[-window:])
    path, ma = [ma_now], ma_now
    for _ in range(days):
        leaving = pending.pop(0) if pending else price
        ma += (price - leaving) / window
        pending.append(price)
        path.append(ma)
    return path


def days_to_cross(
    closes: list[float],
    ma_now: float,
    price: float,
    window: int = MA_DAYS,
    max_days: int = PROJECT_MAX_DAYS,
) -> int | None:
    """주가가 `price`로 횡보할 때 이평선과 만나기까지의 거래일 수.

    주가가 이평선 아래면 이평선이 내려와 만나는 날, 위면 이평선이 올라와 닿는 날이다.
    상한 안에 만나지 못하면 None.
    """
    below = price < ma_now
    path = project_ma_path(closes, ma_now, price, max_days, window)
    # 누적 가감산이라 정확히 만나는 날에도 끝자리 오차가 남는다. 상대 허용오차로 비교한다.
    tol = abs(price) * 1e-9
    if abs(ma_now - price) <= tol:
        return 0
    for day, ma in enumerate(path):
        if day and ((below and ma - price <= tol) or (not below and price - ma <= tol)):
            return day
    return None


def price_for_cross_in(
    closes: list[float], ma_now: float, days: int, window: int = MA_DAYS
) -> float | None:
    """`days` 거래일 뒤 이평선과 만나려면 주가가 얼마여야 하나.

    `days < window`이면 닫힌 해가 있다. days일 뒤 이평선은
    `ma_now + (days·p - S)/window` (S = 그 사이 빠져나갈 종가 합)이고, 이것이 p와 같아지는
    p를 풀면 된다. `days >= window`면 이평선이 곧 p가 되어 해가 무의미하므로 None.
    """
    if days <= 0 or days >= window:
        return None
    leaving = sum(closes[-window:][:days])
    return (ma_now - leaving / window) / (1 - days / window)


def days_to_flat_slope(
    closes: list[float],
    ma_now: float,
    price: float,
    window: int = MA_DAYS,
    max_days: int = PROJECT_MAX_DAYS,
) -> int | None:
    """주가 횡보 가정에서 이평선 기울기가 '평탄' 밴드에 들어가기까지의 거래일 수."""
    path = project_ma_path(closes, ma_now, price, max_days, window)
    for day in range(SLOPE_WINDOW, len(path)):
        base = path[day - SLOPE_WINDOW]
        if not base:
            continue
        if abs((path[day] - base) / base * 100) <= SLOPE_FLAT_PCT:
            return day
    return None


def project(bars: list[dict], window: int = MA_DAYS) -> dict:
    """현재가 횡보 가정과 목표 시점별 필요 주가를 함께 낸다."""
    closes = [b["close"] for b in bars]
    ma_now = sma(closes, window)[-1]
    price = closes[-1]
    if ma_now is None:
        return {}
    below = price < ma_now
    targets = []
    for days in PROJECT_HORIZONS:
        needed = price_for_cross_in(closes, ma_now, days, window)
        if needed is None or needed <= 0:
            continue
        targets.append({"days": days, "price": needed, "change_pct": (needed - price) / price * 100})
    return {
        "direction": (
            "현재 이평선과 일치" if math.isclose(price, ma_now, rel_tol=1e-9)
            else "아래에서 이평선 접촉" if below else "위에서 이평선 접촉"
        ),
        "below": below,
        "close": price,
        "ma": ma_now,
        "gap_pct": (price - ma_now) / ma_now * 100,
        "flat_days": days_to_cross(closes, ma_now, price, window),
        "flat_slope_days": days_to_flat_slope(closes, ma_now, price, window),
        "targets": targets,
        "dropouts": dropout_schedule(closes, window),
    }


# --- 영업이익 증가율 --------------------------------------------------------


def _yoy(rows_by_key: dict, year: int, quarter: int) -> float | None:
    """같은 분기 전년 대비 영업이익 증가율 %. 전년 기준이 0 이하면 계산하지 않는다."""
    cur = rows_by_key.get((year, quarter))
    prev = rows_by_key.get((year - 1, quarter))
    if cur is None or prev is None or prev <= 0:
        return None
    return (cur - prev) / prev * 100


def op_growth(financials: dict | None, primary: str) -> dict:
    """최근 확정 분기와 다음 추정 분기의 영업이익 증가율(YoY)을 비교한다.

    리포트 p.23의 업종 판정과 같은 방식이다 — 직전 발표치 증가율보다 다음 추정치 증가율이
    낮으면 "하락", 높으면 "상승". 어닝 서프라이즈는 쓰지 않는다 (p.18).
    """
    result = {
        "available": False,
        "direction": "판정 불가",
        "latest": None,
        "next": None,
        "note": None,
    }
    if not financials:
        result["note"] = "영업이익 데이터 없음"
        return result

    actual, estimate = stockeasy.fs_rows(financials, primary, yearly=False)
    values = {}
    for row in actual:
        oi, year, quarter = row.get("operating_income"), row.get("year"), row.get("quarter")
        if oi is not None and year and quarter:
            values[(year, quarter)] = oi
    if not values:
        result["note"] = "확정 분기 영업이익 없음"
        return result

    latest_key = max(values)
    # 추정은 확정보다 뒤 분기만 쓴다. 같은 분기가 확정·추정 양쪽에 있으면 확정이 이긴다.
    est_values = {}
    for row in estimate:
        oi, year, quarter = row.get("operating_income"), row.get("year"), row.get("quarter")
        if oi is not None and year and quarter and (year, quarter) > latest_key:
            est_values[(year, quarter)] = oi
    merged = {**values, **est_values}

    latest_yoy = _yoy(merged, *latest_key)
    # 경로 C의 실적 게이트. 증가율과 달리 금액 비교라 적자 축소도 증가로 판정한다.
    prior = values.get((latest_key[0] - 1, latest_key[1]))
    result["latest"] = {
        "period": f"{latest_key[0]}.{latest_key[1]}Q", "kind": "확정", "yoy": latest_yoy,
        "increased": None if prior is None else values[latest_key] > prior,
    }

    next_key = (latest_key[0] + 1, 1) if latest_key[1] == 4 else (latest_key[0], latest_key[1] + 1)
    if next_key not in est_values:
        result["note"] = "다음 분기 컨센 추정 없음"
        return result

    next_yoy = _yoy(merged, *next_key)
    result["next"] = {"period": f"{next_key[0]}.{next_key[1]}Q", "kind": "추정 E", "yoy": next_yoy}

    if latest_yoy is None or next_yoy is None:
        result["note"] = "전년 동기 영업이익이 0 이하 — 증가율 계산 불가"
        return result

    result["available"] = True
    result["direction"] = (
        "평탄" if math.isclose(next_yoy, latest_yoy, rel_tol=0.0, abs_tol=1e-12)
        else "상승" if next_yoy > latest_yoy else "하락"
    )
    return result


# --- 단계 판정 -------------------------------------------------------------


def classify(price: dict, cyclical: dict, growth: dict, close: float) -> dict:
    """가격 3요소로 단계를 확정하고, 영업이익 증가율로 강·약 등급을 매긴다."""
    position, slope, swing = price["position"], price["slope"], price["swing"]
    up = sum(1 for v in (position == "위", slope == "상승", swing == "높아짐") if v)
    down = sum(1 for v in (position == "아래", slope == "하락", swing == "낮아짐") if v)
    reasons = []

    axis = f"위치 {position} · 기울기 {slope} · 스윙 {swing}"
    # 리포트의 1·3단계는 **주가가 이평선을 오르내리는** 상태다 (p.5, p.8). 주가가 이평선 위나
    # 아래에 붙어 있으면 1·3단계 후보가 아니다 — 위면 2단계, 아래면 4단계. 이평선 기울기와
    # 스윙은 확인 지표로 쓰고, 어긋나면 근거에 남기고 확신도를 낮춘다.
    if position == "위":
        stage = 2
        reasons.append(f"주가가 150일선 위에 체류 → 2단계 ({axis})")
    elif position == "아래":
        stage = 4
        reasons.append(f"주가가 150일선 아래에 체류 → 4단계 ({axis})")
    elif slope == "상승" and swing == "높아짐":
        stage = 2
        reasons.append(f"이평선 오르내리나 이평선 상승 + 저점·고점 높아짐 → 2단계 초입 ({axis})")
    elif slope == "하락" and swing == "낮아짐":
        stage = 4
        reasons.append(f"이평선 오르내리나 이평선 하락 + 저점·고점 낮아짐 → 4단계 진입 ({axis})")
    else:
        band = price["band"]
        if band == "하단":
            stage = 1
        elif band == "상단":
            stage = 3
        else:
            # 밴드 중단의 횡보. 1년 방향이 위였으면 정점권(3), 아래였으면 바닥권(1)으로 본다.
            stage = 3 if price["long_trend_up"] else 1
        reasons.append(f"가격 축 혼재 ({axis}) → 52주 밴드 {band}권 기준 {stage}단계")

    # 기울기가 단계와 어긋날 때, **어긋난 방향에 따라 뜻이 정반대다.**
    # 주가가 위인데 이평선이 평탄 = 상승세가 식은 후반부. 이평선이 아직 하락 = 바닥에서 갓
    # 올라탄 초입부. 하나의 문구로 뭉뚱그리면 후반과 초입을 뒤바꿔 읽게 된다.
    if stage == 2 and slope == "평탄":
        reasons.append(
            "⚠️ 150일선 기울기 평탄 — 상승세가 식은 2단계 후반이거나 3단계 이행 구간일 수 있다"
        )
    elif stage == 2 and slope == "하락":
        reasons.append(
            "⚠️ 150일선이 아직 하락 중 — 4단계 이후 바닥에서 갓 올라탄 1단계 후반~2단계 초입일 "
            "수 있다. 이평선 상승 전환이 확인 조건이다"
        )
    if stage == 4 and slope == "평탄":
        reasons.append(
            "⚠️ 150일선 기울기 평탄 — 하락세가 멎은 4단계 후반이거나 1단계 이행 구간일 수 있다"
        )
    elif stage == 4 and slope == "상승":
        reasons.append(
            "⚠️ 150일선이 아직 상승 중 — 2·3단계에서 갓 이탈한 4단계 초입일 수 있다. "
            "이평선 하락 전환이 확인 조건이다"
        )
    if (stage == 2 and swing == "낮아짐") or (stage == 4 and swing == "높아짐"):
        reasons.append(f"⚠️ 스윙({swing})이 단계와 어긋난다 — 확신도 하향, 전환 신호일 수 있다")

    # 경계 규칙 (리포트 p.20~21)
    boundary = None
    cyc_high = (cyclical.get("high") or {}).get("price")
    cyc_low = (cyclical.get("low") or {}).get("price")
    if stage == 3 and cyc_high and close > cyc_high:
        stage, boundary = 2, f"직전 순환적 고점 {cyc_high:,.0f}원 상향 돌파 → 재차 2단계"
    elif stage == 1 and cyc_low and close < cyc_low:
        stage, boundary = 4, f"직전 순환적 저점 {cyc_low:,.0f}원 하향 이탈 → 재차 4단계"
    if boundary:
        reasons.append(boundary)

    # 영업이익 축 (미너비니 확장) — 강·약 등급만 조정한다. 단계 숫자는 바꾸지 않는다.
    label, weak = STAGE_LABEL[stage], False
    if growth["available"] and growth["direction"] in ("상승", "하락"):
        if stage == 2 and growth["direction"] == "하락":
            weak = True
            reasons.append("영업이익 증가율 하락 → 약한 2단계 (리포트 도표 18)")
        elif stage == 4 and growth["direction"] == "상승":
            weak = True
            reasons.append("영업이익 증가율 상승 → 약한 4단계 (리포트 도표 20)")
        else:
            reasons.append(f"영업이익 증가율 {growth['direction']} — 가격 축과 정합")
    elif growth["available"] and growth["direction"] == "평탄":
        reasons.append("영업이익 증가율 평탄 — 강·약 등급 조정 없음")
    else:
        reasons.append(f"가격 전용 판정 — {growth.get('note') or '영업이익 축 미적용'}")

    if weak:
        label = f"약한 {label}"

    aligned = max(up, down)
    growth_aligned = growth["available"] and (
        (stage == 2 and growth["direction"] == "상승")
        or (stage == 4 and growth["direction"] == "하락")
    )
    if aligned == 3 and growth_aligned and not weak:
        confidence = "높음"
    elif aligned >= 2:
        confidence = "중간"
    else:
        confidence = "낮음"

    action = STAGE_ACTION[stage]
    if weak and stage == 2:
        action += " 단 약한 2단계이므로 전형적 2단계 종목보다 비중을 낮춘다 (리포트 p.21)."
    elif weak and stage == 4:
        action += " 단 약한 4단계이므로 1단계 전환 후보로 감시한다."

    return {
        "stage": stage,
        "label": label,
        "weak": weak,
        "confidence": confidence,
        "price_score": {"up": up, "down": down},
        "reasons": reasons,
        "action": action,
    }


def analyze(bars: list[dict], financials: dict | None, primary: str) -> dict:
    """일봉과 재무를 받아 판정 결과 dict를 만든다 (네트워크 없음 — 테스트 대상)."""
    closes = [b["close"] for b in bars]
    ma = sma(closes, MA_DAYS)
    ma_ref = sma(closes, MA_REF_DAYS)
    position, ratio, sample = price_vs_ma(closes, ma)
    slope, slope_pct = ma_slope(ma)
    ref_slope, ref_slope_pct = ma_slope(ma_ref)
    # 「기술적 분석 규칙 1」의 실제 주봉 종가 20주 평균.
    weekly = weekly_sma(bars)
    ma20w_value = weekly["value"]
    ma20w_slope, ma20w_slope_pct = weekly["slope"], weekly["slope_pct"]
    if ma20w_value is None:
        ma20w_position, ma20w_ratio = "판정 불가", None
    else:
        ma20w_position = "위" if closes[-1] > ma20w_value else "아래"
        ma20w_ratio = (closes[-1] / ma20w_value - 1) * 100
    pivot_highs, pivot_lows = swing_pivots(bars)
    swing = swing_trend(pivot_highs, pivot_lows)
    band, band_ratio = band_position(bars)
    long_trend_up = closes[-1] > closes[max(0, len(closes) - TREND_WINDOW)]

    price = {
        "position": position,
        "position_ratio": ratio,
        "position_sample": sample,
        "slope": slope,
        "slope_pct": slope_pct,
        "ref_slope": ref_slope,
        "ref_slope_pct": ref_slope_pct,
        "swing": swing,
        "band": band,
        "band_ratio": band_ratio,
        "long_trend_up": long_trend_up,
        "ma": ma[-1],
        "ma_ref": ma_ref[-1],
        "ma20w": ma20w_value,
        "ma20w_slope": ma20w_slope,
        "ma20w_slope_pct": ma20w_slope_pct,
        "ma20w_position": ma20w_position,
        "ma20w_ratio": ma20w_ratio,
        "close": closes[-1],
        "long_bull": long_bull_days(bars),
        "pivot_highs": pivot_highs[-2:],
        "pivot_lows": pivot_lows[-2:],
    }
    cyclical = cyclical_levels(bars, pivot_highs, pivot_lows)
    growth = op_growth(financials, primary)
    verdict = classify(price, cyclical, growth, closes[-1])
    return {
        "price": price,
        "cyclical": cyclical,
        "growth": growth,
        "box": box_range(bars),
        "volume_bias": volume_bias(bars),
        "verdict": verdict,
        "bars": len(bars),
        "last_date": bars[-1]["date"],
    }


# --- 출력 -----------------------------------------------------------------


def _pct(value, digits: int = 1) -> str:
    return "-" if value is None else f"{value:+.{digits}f}%"


def print_result(name: str, code: str, result: dict, sources: list[str]) -> None:
    v, p, g = result["verdict"], result["price"], result["growth"]
    print(
        f"=== Stage Analysis — {name}({code}) === 조회 {date.today().isoformat()} · "
        f"최종봉 {result['last_date']} · 출처 {' + '.join(sources)}"
    )
    print(f"판정: {v['label']} · 확신도 {v['confidence']}")
    gap = (p["close"] - p["ma"]) / p["ma"] * 100 if p["ma"] else None
    print(
        f"  주가 vs {MA_DAYS}일선: {p['position']}"
        + (f" (최근 {p['position_sample']}일 중 {p['position_ratio'] * 100:.0f}%)" if p["position_ratio"] is not None else "")
        + f" · 현재가 {p['close']:,.0f}원 / {MA_DAYS}MA "
        + (f"{p['ma']:,.0f}원 ({_pct(gap)})" if p["ma"] else "-")
    )
    print(
        f"  {MA_DAYS}일선 기울기: {p['slope']} ({_pct(p['slope_pct'])} / {SLOPE_WINDOW}일)"
        f" · 참고 {MA_REF_DAYS}일선: {p['ref_slope']} ({_pct(p['ref_slope_pct'])})"
    )
    gap20w = (p["close"] / p["ma20w"] - 1) * 100 if p["ma20w"] else None
    print(
        f"  20주선(실제 주봉 종가 {WEEK_COUNT}주; 기존 일봉 근사 {MA_20WEEK}일, 「기술적 분석 규칙 1」): "
        + (f"{p['ma20w']:,.0f}원 ({_pct(gap20w)})" if p["ma20w"] else "-")
        + f" · 방향 {p['ma20w_slope']} ({_pct(p['ma20w_slope_pct'])} / 1주)"
        f" · 주가 {p['ma20w_position']}"
    )
    if p["long_bull"]:
        days = " · ".join(
            f"{d['date']} {d['change']:+.1f}% (종가 {d['close']:,.0f}원)" for d in p["long_bull"]
        )
        print(f"  장대 양봉(「매매규칙 12」 +{LONG_BULL_PCT:.0f}% 이상, 최근 {LONG_BULL_WINDOW}일): {days}")
    else:
        print(f"  장대 양봉(최근 {LONG_BULL_WINDOW}일): 없음")
    highs = " → ".join(f"{h['price']:,.0f}" for h in p["pivot_highs"]) or "-"
    lows = " → ".join(f"{l['price']:,.0f}" for l in p["pivot_lows"]) or "-"
    print(f"  스윙: 저점·고점 {p['swing']} (고점 {highs} / 저점 {lows})")
    box = result["box"]
    print(
        f"  박스권({box['days']}일): {box['bottom']:,.0f} ~ {box['top']:,.0f}원"
        + (f" (폭 {box['width_pct']:.1f}%)" if box["width_pct"] is not None else "")
        + f" · 52주 밴드 {p['band']}권"
    )
    for key, ko, cmp_text in (("high", "고점", "돌파"), ("low", "저점", "이탈")):
        level = result["cyclical"].get(key)
        if not level:
            continue
        crossed = p["close"] > level["price"] if key == "high" else p["close"] < level["price"]
        print(
            f"  직전 순환적 {ko}: {level['price']:,.0f}원 ({level['date']}) — "
            f"{'' if crossed else '미'}{cmp_text}"
        )
    if g["latest"]:
        head = f"  영업이익 증가율(YoY): {g['latest']['period']} 확정 {_pct(g['latest']['yoy'])}"
        if g["next"]:
            head += f" → {g['next']['period']} 추정 E {_pct(g['next']['yoy'])} = {g['direction']}"
        print(head)
        increased = g["latest"]["increased"]
        print("    전년 동기 대비 " + (
            "모름 (전년 동기 확정치 없음 — 경로 C 실적 게이트 판정 불가)" if increased is None
            else "증가 (경로 C 실적 게이트 통과)" if increased
            else "감소 (경로 C 실적 게이트 미통과)"
        ))
    if g["note"]:
        print(f"    ⚠️ {g['note']}")
    if result["volume_bias"] is not None:
        print(f"  거래량(보조): 상승일/하락일 평균 = {result['volume_bias']:.2f}배")
    print("근거:")
    for reason in v["reasons"]:
        print(f"  - {reason}")
    print(f"대응: {v['action']}")


def _months(days: int) -> str:
    return f"약 {days / TRADING_DAYS_PER_MONTH:.1f}개월"


def print_projection(proj: dict) -> None:
    """이동평균 교차 전망. 주가를 현 수준으로 고정한 **산술 계산**이지 예측이 아니다."""
    if not proj:
        return
    print(f"[전망] {proj['direction']} — 현재가 {proj['close']:,.0f}원 유지 가정")
    print("  이평선 접촉의 산술 계산이며 단계 전환을 뜻하지 않는다. 미래 가격 예측이 아니다.")
    if proj["flat_days"] is not None:
        d = proj["flat_days"]
        print(f"  주가 횡보 시 {MA_DAYS}일선 접촉: {d}거래일 뒤 ({_months(d)})")
    else:
        print(f"  주가 횡보 시 {PROJECT_MAX_DAYS}거래일 안에는 {MA_DAYS}일선과 만나지 않는다")
    if proj["flat_slope_days"] is not None:
        d = proj["flat_slope_days"]
        print(f"  주가 횡보 시 기울기 '평탄' 진입: {d}거래일 뒤 ({_months(d)})")
    if proj["targets"]:
        print("  N거래일 뒤 이평선과 같아지는 주가 (그 기간 그 가격 유지 가정):")
        for t in proj["targets"]:
            print(
                f"    {t['days']:>3d}거래일({_months(t['days'])}): "
                f"{t['price']:>10,.0f}원 ({t['change_pct']:+.1f}%)"
            )
    if proj["dropouts"]:
        print(f"  {MA_DAYS}일선에서 빠져나갈 과거 종가 (클수록 이평선이 저절로 내려온다):")
        for d in proj["dropouts"]:
            print(f"    +{d['from_day']:>3d}~{d['to_day']:>3d}일: {d['avg']:>10,.0f}원")


# --- main ------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage Analysis 단계 판정 (통합 버전)")
    parser.add_argument("query", help="종목명 또는 6자리 티커")
    parser.add_argument("--no-cache", action="store_true", help="캐시를 쓰지 않고 매번 새로 받는다 (캐시 오염 의심 시)")
    parser.add_argument("--days", type=int, default=1100, help="일봉 조회 일수 (기본 1100 ≈ 3년)")
    parser.add_argument("--no-fundamental", action="store_true", help="영업이익 축 없이 가격만으로 판정")
    parser.add_argument(
        "--project",
        action="store_true",
        help="이동평균 교차 시점 전망 — 현재가 횡보 가정과 시점별 필요 주가",
    )
    parser.add_argument("--json", action="store_true", help="판정 결과 JSON 덤프")
    args = parser.parse_args(argv)
    if args.no_cache:
        http_cache.disable()

    # 티커 해석 경로는 하나다 — `context/ticker_overrides.md`가 검색 API보다 먼저다.
    stock, err, code = tickers.resolve_stock(args.query)
    if err:
        print(f"ERROR: {err}", file=sys.stderr)
        return code
    ticker = stock["stock_code"]

    bars, e, bar_note = daily.fetch_daily_bars(ticker, args.days)
    if e or not bars:
        print(f"ERROR: 시세 수집 실패 — {e or '빈 응답'} ({bar_note or 'StockEasy 일봉'}, {ticker})", file=sys.stderr)
        return 1
    if len(bars) < MA_DAYS + SLOPE_WINDOW:
        print(
            f"ERROR: 일봉 {len(bars)}개 — {MA_DAYS}일선 판정에 부족하다 "
            f"(최소 {MA_DAYS + SLOPE_WINDOW}개 필요, --days 확대)",
            file=sys.stderr,
        )
        return 1

    sources = [bar_note or "StockEasy 일봉"]
    financials, primary, name = None, "C", stock.get("stock_name") or ticker
    if not args.no_fundamental:
        # 우선주는 분기 실적이 따로 없다 — 영업이익 축만 본주 코드로 받는다. 일봉·이동평균은
        # 위에서 이미 우선주 자기 시세로 받았고, 괴리율이 따로 움직이므로 그대로 둔다.
        fs_ticker, is_preferred = tickers.fundamentals_code(ticker)
        cookie = stockeasy.load_cookie()
        info, ie = stockeasy.fetch_stock_json(
            stockeasy.ENDPOINTS["info_tab"].format(code=fs_ticker),
            referer=f"{stockeasy.PAGE_BASE}/{fs_ticker}",
            cookie=cookie,
        )
        if info:
            financials = info.get("financials")
            primary = info.get("primary_fs_type") or "C"
            # 우선주면 info_tab이 본주 이름을 들고 온다 — 표시 이름은 우선주 그대로 두고
            # 그 이름은 출처 꼬리표에만 쓴다.
            fetched_name = (info.get("stock_info") or {}).get("name")
            if is_preferred:
                label = f"{fetched_name}({fs_ticker})" if fetched_name else fs_ticker
                sources.append(f"우선주 실적은 본주 {label}")
            else:
                name = fetched_name or name
            sources.append("StockEasy info-tab")
        else:
            hint = "쿠키 만료·무효" if cookie else f"{stockeasy.COOKIE_ENV} 미설정"
            reason = f"{ie or '빈 응답'}" + (f" ({hint})" if ie == "HTTP 401" or not cookie else "")
            print(f"[누락] info_tab — {reason} · 가격 전용 판정으로 진행", file=sys.stderr)

    result = analyze(bars, financials, primary)
    if args.project:
        result["projection"] = project(bars)

    if args.json:
        print(
            json.dumps(
                {"stock_code": ticker, "stock_name": name, "fetched_at": date.today().isoformat(),
                 "sources": sources, **result},
                ensure_ascii=False,
                default=str,
            )
        )
        return 0

    print_result(name, ticker, result, sources)
    if args.project:
        print_projection(result.get("projection") or {})
    return 0


if __name__ == "__main__":
    sys.exit(main())
