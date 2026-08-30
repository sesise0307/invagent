#!/usr/bin/env python3
"""Stage Analysis 단계 판정기 (통합 버전).

개별 종목이 주가 성숙 4단계 중 어디에 있는지 **결정론적으로** 판정한다.
근거는 DB증권 「Stage Analysis 마스터하기」(2026-08-25) III·IV장의 통합 버전이다.
스탠 와인스타인의 가격·이동평균 축에 마크 미너비니의 영업이익 증가율 축을 얹는다.

데이터 소스 둘:

1. 일봉 OHLCV — 네이버 금융 `siseJson` (무인증). 150일 이동평균·기울기·스윙 고저점·박스권.
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
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

_STOCK_INFO_DIR = Path(__file__).resolve().parents[2] / "analyze-stock" / "scripts"
if str(_STOCK_INFO_DIR) not in sys.path:
    sys.path.insert(0, str(_STOCK_INFO_DIR))

import fetch_stock_info as si_api  # noqa: E402  (경로 주입 후에만 import된다)

SISE_URL = "https://api.finance.naver.com/siseJson.naver"
TIMEOUT = 20

# --- 판정 임계값 -----------------------------------------------------------
# 리포트는 "150일선 위/아래", "평탄", "저점·고점이 높아진다"를 말로만 규정하고 수치를 주지
# 않는다. 아래 값은 이 스킬이 정한 운영 기준이다. 바꾸려면 SKILL.md의 근거도 함께 고친다.
MA_DAYS = 150            # 통합 버전 기준선. 200일선은 참고로 병기만 한다 (리포트 p.17~18).
MA_REF_DAYS = 200        # 미너비니 원본 기준선 — 참고 표시용
SLOPE_WINDOW = 20        # 기울기 측정 구간 (거래일)
SLOPE_FLAT_PCT = 1.5     # ±1.5%/20일 이내면 "평탄"
POSITION_WINDOW = 20     # 주가-이평선 위치 판정 구간 (거래일)
POSITION_ABOVE = 0.8     # 최근 20일 중 80% 이상 위 → "위"
POSITION_BELOW = 0.2     # 20% 이하 → "아래", 그 사이는 "오르내림"
PIVOT_K = 10             # 스윙 피벗 프랙탈 반경 (좌우 거래일)
BOX_WINDOW = 60          # 박스권 상·하단 측정 구간 (거래일)
CYCLE_WINDOW = 250       # 순환적 고점·저점 탐색 구간 (거래일 ≈ 1년)
TREND_WINDOW = 250       # 중간 구간 1·3단계 가르기용 장기 방향 구간

STAGE_LABEL = {
    1: "1단계 (기초 지역 / 무시 국면)",
    2: "2단계 (상승 국면 / 매집)",
    3: "3단계 (최정상 지역 / 분산)",
    4: "4단계 (쇠퇴 국면 / 투매)",
}

STAGE_ACTION = {
    1: "기다리며 지켜본다. 매수 대상이 아니다 — 「기본 원칙 8(풍림화산)」 대기 구간.",
    2: "매수 대상 구간. 「매매규칙 2」의 '바닥 다진 후 고개 드는 초반'은 1단계 후반~2단계 초입이다.",
    3: "신규 매수 금지. 「매매규칙 11」 수익 쿠션 확보 · 「매매규칙 5」 분할 매도 구간.",
    4: "접근에 신중해야 한다. 「매매규칙 6」 -15% 손절 · 「매매규칙 8」 물타기 금지.",
}


# --- 시세 수집 -------------------------------------------------------------


def parse_sise(text: str) -> list[dict]:
    """`siseJson` 응답을 일봉 리스트로 바꾼다.

    응답은 표준 JSON이 아니라 작은따옴표를 쓴 파이썬 리터럴이다. 헤더 행을 버리고
    날짜 오름차순 리스트를 돌려준다.
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


def fetch_bars(code: str, days: int) -> tuple[list[dict], str | None]:
    """네이버 일봉을 받아온다. 실패하면 ([], 사유)."""
    end = date.today()
    start = end - timedelta(days=days)
    params = (
        f"?symbol={code}&requestType=1"
        f"&startTime={start.strftime('%Y%m%d')}&endTime={end.strftime('%Y%m%d')}&timeframe=day"
    )
    req = urllib.request.Request(
        SISE_URL + params,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return parse_sise(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        return [], f"HTTP {e.code}"
    except Exception as e:  # 네트워크 오류·파싱 실패
        return [], str(e)[:80]


# --- 가격 지표 (순수 함수) --------------------------------------------------


def sma(values: list[float], window: int) -> list[float | None]:
    """단순이동평균. 구간이 안 차는 앞부분은 None."""
    out: list[float | None] = []
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= window:
            total -= values[i - window]
        out.append(total / window if i >= window - 1 else None)
    return out


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


def price_vs_ma(
    closes: list[float], ma: list[float | None], window: int = POSITION_WINDOW
) -> tuple[str, float | None, int]:
    """최근 구간 중 종가가 이평선 위에 머문 비율 → ("위"/"아래"/"오르내림", 비율, 표본수)."""
    pairs = [(c, m) for c, m in zip(closes, ma) if m is not None][-window:]
    if not pairs:
        return "판정 불가", None, 0
    above = sum(1 for c, m in pairs if c > m)
    ratio = above / len(pairs)
    if ratio >= POSITION_ABOVE:
        return "위", ratio, len(pairs)
    if ratio <= POSITION_BELOW:
        return "아래", ratio, len(pairs)
    return "오르내림", ratio, len(pairs)


def swing_pivots(bars: list[dict], k: int = PIVOT_K) -> tuple[list[dict], list[dict]]:
    """좌우 k봉 프랙탈 고점·저점. 최근 k봉은 확정되지 않아 제외된다."""
    highs, lows = [], []
    for i in range(k, len(bars) - k):
        window = bars[i - k : i + k + 1]
        if bars[i]["high"] >= max(b["high"] for b in window):
            highs.append({"date": bars[i]["date"], "price": bars[i]["high"], "index": i})
        if bars[i]["low"] <= min(b["low"] for b in window):
            lows.append({"date": bars[i]["date"], "price": bars[i]["low"], "index": i})
    return highs, lows


def swing_trend(pivot_highs: list[dict], pivot_lows: list[dict]) -> str:
    """최근 두 개의 스윙 고점·저점으로 계단 방향을 읽는다."""
    if len(pivot_highs) < 2 or len(pivot_lows) < 2:
        return "판정 불가"
    hh = pivot_highs[-1]["price"] > pivot_highs[-2]["price"]
    hl = pivot_lows[-1]["price"] > pivot_lows[-2]["price"]
    if hh and hl:
        return "높아짐"
    if not hh and not hl:
        return "낮아짐"
    return "혼조"


def box_range(bars: list[dict], window: int = BOX_WINDOW) -> dict:
    """최근 구간의 박스권 상·하단과 폭."""
    recent = bars[-window:]
    top = max(b["high"] for b in recent)
    bottom = min(b["low"] for b in recent)
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
    """최근 1년 밴드에서 현재가가 어디인가 → ("하단"/"중단"/"상단", 위치 비율)."""
    recent = bars[-window:]
    top = max(b["high"] for b in recent)
    bottom = min(b["low"] for b in recent)
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

    actual, estimate = si_api._fs_rows(financials, primary, yearly=False)
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
    result["latest"] = {"period": f"{latest_key[0]}.{latest_key[1]}Q", "kind": "확정", "yoy": latest_yoy}

    if not est_values:
        result["note"] = "다음 분기 컨센 추정 없음"
        if latest_yoy is not None:
            result["available"] = True
        return result

    next_key = min(est_values)
    next_yoy = _yoy(merged, *next_key)
    result["next"] = {"period": f"{next_key[0]}.{next_key[1]}Q", "kind": "추정 E", "yoy": next_yoy}

    if latest_yoy is None or next_yoy is None:
        result["note"] = "전년 동기 영업이익이 0 이하 — 증가율 계산 불가"
        return result

    result["available"] = True
    result["direction"] = "상승" if next_yoy > latest_yoy else "하락"
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
    else:
        reasons.append(f"가격 전용 판정 — {growth.get('note') or '영업이익 축 미적용'}")

    if weak:
        label = f"약한 {label}"

    aligned = max(up, down)
    if aligned == 3 and growth["available"] and not weak:
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
        "close": closes[-1],
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
    if g["note"]:
        print(f"    ⚠️ {g['note']}")
    if result["volume_bias"] is not None:
        print(f"  거래량(보조): 상승일/하락일 평균 = {result['volume_bias']:.2f}배")
    print("근거:")
    for reason in v["reasons"]:
        print(f"  - {reason}")
    print(f"대응: {v['action']}")


# --- main ------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage Analysis 단계 판정 (통합 버전)")
    parser.add_argument("query", help="종목명 또는 6자리 티커")
    parser.add_argument("--days", type=int, default=1100, help="일봉 조회 일수 (기본 1100 ≈ 3년)")
    parser.add_argument("--no-fundamental", action="store_true", help="영업이익 축 없이 가격만으로 판정")
    parser.add_argument("--json", action="store_true", help="판정 결과 JSON 덤프")
    args = parser.parse_args(argv)

    stock, err, code = si_api.resolve_stock(args.query)
    if err:
        print(f"ERROR: {err}", file=sys.stderr)
        return code
    ticker = stock["stock_code"]

    bars, e = fetch_bars(ticker, args.days)
    if e or not bars:
        print(f"ERROR: 시세 수집 실패 — {e or '빈 응답'} (네이버 siseJson, {ticker})", file=sys.stderr)
        return 1
    if len(bars) < MA_DAYS + SLOPE_WINDOW:
        print(
            f"ERROR: 일봉 {len(bars)}개 — {MA_DAYS}일선 판정에 부족하다 "
            f"(최소 {MA_DAYS + SLOPE_WINDOW}개 필요, --days 확대)",
            file=sys.stderr,
        )
        return 1

    sources = ["Naver siseJson"]
    financials, primary, name = None, "C", stock.get("stock_name") or ticker
    if not args.no_fundamental:
        cookie = si_api.load_cookie()
        info, ie = si_api.fetch_json(
            si_api.ENDPOINTS["info_tab"].format(code=ticker),
            referer=f"{si_api.PAGE_BASE}/{ticker}",
            cookie=cookie,
        )
        if info:
            financials = info.get("financials")
            primary = info.get("primary_fs_type") or "C"
            name = (info.get("stock_info") or {}).get("name") or name
            sources.append("StockEasy info-tab")
        else:
            hint = "쿠키 만료·무효" if cookie else f"{si_api.COOKIE_ENV} 미설정"
            reason = f"{ie or '빈 응답'}" + (f" ({hint})" if ie == "HTTP 401" or not cookie else "")
            print(f"[누락] info_tab — {reason} · 가격 전용 판정으로 진행", file=sys.stderr)

    result = analyze(bars, financials, primary)

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
