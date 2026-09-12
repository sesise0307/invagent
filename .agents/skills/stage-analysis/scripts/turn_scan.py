#!/usr/bin/env python3
"""단기 바닥 전환 판정기.

「매매규칙 2」의 "하락하는 와중에 매수하지 않고 바닥을 다진 후 고개를 들기 시작하는 초반"을
일봉 20~60일 창으로 **결정론적으로** 판정한다. 150일선 스테이지(`stage_scan.py`)는 주도주의
국면에는 맞지만 가치·소외주의 바닥에서는 전환 확인이 수개월 늦다 — 그 공백을 메우는 판정기다.

상태 넷 (모든 수준은 종가로 잡는다 — `context/my_rules.md` 「적용 방법」):

- falling  하락 중      최근 `BASE_MIN_AGE`일 안에 `BASE_LOOKBACK`일 최저 종가를 새로 썼다
- basing   바닥 다지기  저점 이후 신저가 없음, 고개 들기 조건 일부 미충족
- turning  고개 들기    저점 높임 + 반등 고점 종가 돌파 + 상승하는 20일선 위 + 앵커드 VWAP 위
- extended 초입 지남    저점이나 돌파선에서 이미 멀리 올라왔다 — 추격하지 않는다

근거 기법은 `references/turn_theory.md` (와이코프 매집 · Sperandeo 1-2-3 · 앵커드 VWAP).

데이터 소스: 네이버 금융 `siseJson` 일봉 (무인증, `invagent.datafeed.naver`).
종료 코드: 0 정상 / 1 시세 수집 실패·일봉 부족 / 2 종목명 후보 다수.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from invagent.datafeed import cache as http_cache, naver, series, tickers

# --- 판정 임계값 -----------------------------------------------------------
# 이 스킬이 정한 운영 기준이다. 바꾸려면 SKILL.md 「단기 바닥 전환 판정」 표의 근거도 함께 고친다.
MIN_BARS = 130           # 바닥 탐색 120일 + 스윙 확정 여유. 이보다 짧으면 판정하지 않는다
BASE_LOOKBACK = 120      # 바닥 저점 탐색 구간 (거래일 ≈ 6개월)
DECLINE_MIN_PCT = 20.0   # 구간 최고 종가 대비 이만큼 무너졌으면 바닥은 그 하락 안에서 찾는다
BASE_MIN_AGE = 15        # 저점 이후 신저가 없이 버텨야 하는 최소 거래일 (≈ 3주)
TURN_PIVOT_K = 5         # 단기 스윙 프랙탈 반경 (좌우 거래일). stage_scan의 10일의 절반
MA_SHORT = 20            # 단기 추세선
MA_SHORT_RISE_DAYS = 5   # 20일선 "상승" = 5거래일 전보다 높다
MA_MID = 60              # 2차 증량 트리거의 중기선
MA_MID_SLOPE_DAYS = 20   # 60일선 "상향 전환" = 20거래일 변화율 > 0
EXTENDED_FROM_LOW_PCT = 35.0     # 저점 대비 이만큼 넘게 올랐으면 초입이 아니다
EXTENDED_FROM_PIVOT_PCT = 10.0   # 돌파선 대비 이만큼 넘게 올랐으면 돌파 초입을 지났다
SPRING_TOL_PCT = 3.0     # 바닥을 이 폭 이내로 깼다가 종가로 되찾으면 Spring (와이코프 2~5% 중 보수적)
# 보조 증거 — 확신도만 조정하고 상태는 바꾸지 않는다.
BREAKOUT_VOL_RATIO = 1.5  # 돌파일 거래량 ÷ 직전 VOLUME_AVG_DAYS일 평균
VOLUME_AVG_DAYS = 50
BASE_VOL_RATIO = 1.0      # 바닥 이후 상승일 평균 거래량 ÷ 하락일 평균 거래량
RSI_PERIOD = 14           # Wilder RSI

STATE_LABEL = {
    "falling": "하락 중",
    "basing": "바닥 다지기",
    "turning": "고개 들기",
    "extended": "초입 지남",
}

STATE_ACTION = {
    "falling": "매수 금지 — 「매매규칙 2」 하락하는 와중에 매수하지 않는다. 바닥 저점이 "
               f"{BASE_MIN_AGE}거래일 버틸 때까지 대기.",
    "basing": "관망 — 바닥은 다지는 중이나 아직 고개를 들지 않았다. 돌파선 종가 돌파를 감시한다 "
              "(「기본 원칙 8(풍림화산)」 대기).",
    "turning": "「매매규칙 2」의 '고개 드는 초반' — 밸류 게이트 통과 시 「매매규칙 4」 사전 계획의 "
               "1차(목표 비중 ÷ 3). 진입 가부의 정본은 `analyze-stock` 10단계.",
    "extended": "추격 금지 — 초입을 지났다. 20일선·앵커드 VWAP 눌림에서 저점 높임을 확인한 뒤 재판정.",
}


def anchored_vwap(bars: list[dict], start: int) -> float | None:
    """`start`봉부터 오늘까지의 거래량 가중 평균가 (전형가 = (고+저+종)/3)."""
    weighted = volume = 0.0
    for bar in bars[start:]:
        typical = (bar["high"] + bar["low"] + bar["close"]) / 3
        weighted += typical * bar["volume"]
        volume += bar["volume"]
    return weighted / volume if volume else None


def rsi(closes: list[float], period: int = RSI_PERIOD) -> list[float | None]:
    """Wilder RSI. 구간이 안 차는 앞부분은 None."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = [max(b - a, 0.0) for a, b in zip(closes, closes[1:])]
    losses = [max(a - b, 0.0) for a, b in zip(closes, closes[1:])]

    def value(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        return 100 - 100 / (1 + gain / loss)

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    out[period] = value(avg_gain, avg_loss)
    for i in range(period + 1, len(closes)):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        out[i] = value(avg_gain, avg_loss)
    return out


def _evidence(bars: list[dict], closes: list[float], pivot_lows: list[dict], start: int,
              low_index: int, higher_low: dict | None, breakout: float | None,
              spring: bool) -> dict:
    """보조 증거 넷. 판정할 수 없는 항목은 None — 모르는 것을 통과로도 탈락으로도 세지 않는다."""
    volumes = [float(b["volume"]) for b in bars]

    breakout_volume = None
    if higher_low and breakout is not None:
        day = next((i for i in range(higher_low["index"] + 1, len(closes)) if closes[i] > breakout), None)
        prior = volumes[max(0, day - VOLUME_AVG_DAYS) : day] if day else []
        if prior and sum(prior):
            breakout_volume = volumes[day] / (sum(prior) / len(prior)) >= BREAKOUT_VOL_RATIO

    ups, downs = [], []
    for i in range(low_index + 1, len(closes)):
        (ups if closes[i] >= closes[i - 1] else downs).append(volumes[i])
    base_volume = None
    if ups and downs and sum(downs):
        base_volume = (sum(ups) / len(ups)) / (sum(downs) / len(downs)) >= BASE_VOL_RATIO

    # 강세 다이버전스: 바닥 직전의 스윙 저점보다 가격은 낮은데 RSI는 높다.
    rsi_divergence = None
    earlier = [p for p in pivot_lows
               if start <= p["index"] < low_index and p["price"] > closes[low_index]]
    if earlier:
        series_rsi = rsi(closes)
        then, now = series_rsi[earlier[-1]["index"]], series_rsi[low_index]
        if then is not None and now is not None:
            rsi_divergence = now > then

    return {
        "breakout_volume": breakout_volume,
        "base_volume": base_volume,
        "rsi_divergence": rsi_divergence,
        "spring": spring,
    }


def analyze_turn(bars: list[dict]) -> dict:
    """일봉을 받아 바닥 전환 상태를 판정한다 (네트워크 없음 — 테스트 대상)."""
    if len(bars) < MIN_BARS:
        raise ValueError(f"일봉 {len(bars)}개 — 최소 {MIN_BARS}개 필요")
    closes = [float(b["close"]) for b in bars]
    close = closes[-1]
    start = max(0, len(closes) - BASE_LOOKBACK)
    # 랠리 뒤 급락한 종목을 랠리 전 저점으로 재면 고점 대비 -40%인데도 「초입 지남」이 된다.
    # 구간 최고 종가 이후 DECLINE_MIN_PCT 이상 무너졌으면 바닥은 그 하락 안에서 찾는다.
    peak_index = max(range(start, len(closes)), key=lambda i: closes[i])
    if min(closes[peak_index:]) <= closes[peak_index] * (1 - DECLINE_MIN_PCT / 100):
        start = peak_index
    low_index = min(range(start, len(closes)), key=lambda i: closes[i])

    # Spring: 최근 저점이 그 전 바닥을 SPRING_TOL_PCT 이내로만 깼고 종가가 그 바닥 위로 돌아왔으면
    # 이탈이 아니라 흔들기다. 구조상 바닥은 그 전 바닥으로 두고, 무효화선만 Spring 저점으로 내린다.
    spring_low = None
    prior_end = len(closes) - BASE_MIN_AGE
    if len(closes) - 1 - low_index < BASE_MIN_AGE and prior_end > start:
        prior_index = min(range(start, prior_end), key=lambda i: closes[i])
        prior = closes[prior_index]
        if closes[low_index] >= prior * (1 - SPRING_TOL_PCT / 100) and close > prior:
            spring_low = {"date": bars[low_index]["date"], "price": closes[low_index],
                          "index": low_index}
            low_index = prior_index
    base_low = {
        "date": bars[low_index]["date"],
        "price": closes[low_index],
        "index": low_index,
        "age": len(closes) - 1 - low_index,
    }

    # 저점 높임: 바닥 이후 확정된 마지막 단기 스윙 저점이 바닥보다 높아야 한다.
    _, pivot_lows = series.swing_pivots(bars, TURN_PIVOT_K, high_key="close", low_key="close")
    after = [p for p in pivot_lows if p["index"] > low_index]
    higher_low = after[-1] if after and after[-1]["price"] > base_low["price"] else None
    # 돌파선: 바닥과 저점 높임 사이의 최고 종가 (1-2-3의 2번 점). 피벗 고점 확정을 기다리지 않는다.
    breakout = max(closes[low_index + 1 : higher_low["index"]]) if higher_low else None

    ma20 = series.sma(closes, MA_SHORT)
    ma20_now = ma20[-1]
    ma20_then = ma20[-1 - MA_SHORT_RISE_DAYS] if len(ma20) > MA_SHORT_RISE_DAYS else None
    avwap = anchored_vwap(bars, low_index)
    ma60 = series.sma(closes, MA_MID)
    ma60_then = ma60[-1 - MA_MID_SLOPE_DAYS]
    ma60_slope_pct = (ma60[-1] / ma60_then - 1) * 100 if ma60[-1] and ma60_then else None

    checks = {
        "저점 높임": higher_low is not None,
        "돌파선 종가 돌파": breakout is not None and close > breakout,
        "20일선 위": ma20_now is not None and close > ma20_now,
        "20일선 상승": ma20_now is not None and ma20_then is not None and ma20_now > ma20_then,
        "앵커드 VWAP 위": avwap is not None and close > avwap,
    }

    rise_from_low_pct = (close / base_low["price"] - 1) * 100
    past_pivot_pct = (close / breakout - 1) * 100 if breakout else None
    extended = rise_from_low_pct > EXTENDED_FROM_LOW_PCT or (
        past_pivot_pct is not None and past_pivot_pct > EXTENDED_FROM_PIVOT_PCT
    )

    if base_low["age"] < BASE_MIN_AGE:
        state = "falling"
    elif extended:
        state = "extended"
    elif all(checks.values()):
        state = "turning"
    else:
        state = "basing"

    evidence = _evidence(bars, closes, pivot_lows, start, low_index, higher_low, breakout,
                         spring_low is not None)
    passed = sum(1 for v in evidence.values() if v is True)
    confidence = "높음" if passed >= 2 else "중간" if passed == 1 else "낮음"

    return {
        "state": state,
        "label": STATE_LABEL[state],
        "close": close,
        "last_date": bars[-1]["date"],
        "base_low": base_low,
        "spring": spring_low is not None,
        "spring_low": spring_low,
        # 종가가 이 선 아래로 내려가면 바닥이 깨진 것이다 — 하락 중으로 재판정.
        "invalidation": min(closes[low_index:]),
        "higher_low": higher_low,
        "breakout": breakout,
        "rise_from_low_pct": rise_from_low_pct,
        "past_pivot_pct": past_pivot_pct,
        "ma20": ma20_now,
        "ma20_rising": checks["20일선 상승"],
        "ma60": ma60[-1],
        "ma60_slope_pct": ma60_slope_pct,
        "ma60_rising": ma60_slope_pct is not None and ma60_slope_pct > 0,
        "avwap": avwap,
        "action": STATE_ACTION[state],
        "checks": checks,
        "missing": [name for name, ok in checks.items() if not ok],
        "evidence": evidence,
        "evidence_passed": passed,
        "evidence_known": sum(1 for v in evidence.values() if v is not None),
        "confidence": confidence,
    }


# --- 출력 -----------------------------------------------------------------


def _won(value: float | None) -> str:
    return "-" if value is None else f"{value:,.0f}원"


def _mark(value: bool | None) -> str:
    return "?" if value is None else "✓" if value else "✗"


def print_result(name: str, code: str, r: dict) -> None:
    base = r["base_low"]
    print(
        f"=== 단기 바닥 전환 — {name}({code}) === 조회 {date.today().isoformat()} · "
        f"최종봉 {r['last_date']} · 출처 Naver siseJson"
    )
    unknown = 4 - r["evidence_known"]
    print(
        f"판정: {r['label']} · 확신도 {r['confidence']} "
        f"(보조 증거 {r['evidence_passed']}/4" + (f", 모름 {unknown}" if unknown else "") + ")"
    )
    print(
        f"  바닥 저점: {_won(base['price'])} ({base['date']}, {base['age']}거래일 전) · "
        f"현재가 {_won(r['close'])} (저점 대비 {r['rise_from_low_pct']:+.1f}%)"
    )
    if r["spring_low"]:
        s = r["spring_low"]
        print(f"  Spring: {_won(s['price'])} ({s['date']}) — 바닥을 "
              f"{(s['price'] / base['price'] - 1) * 100:+.1f}% 깼다가 종가로 되찾음")
    hl = r["higher_low"]
    print(f"  저점 높임: " + (f"{_won(hl['price'])} ({hl['date']})" if hl else "없음 (확정된 스윙 저점 없음)"))
    if r["breakout"] is not None:
        crossed = "종가 돌파" if r["close"] > r["breakout"] else "미돌파"
        print(f"  돌파선: {_won(r['breakout'])} — {crossed} (돌파선 대비 {r['past_pivot_pct']:+.1f}%)")
    else:
        print("  돌파선: - (저점 높임이 확정돼야 정해진다)")
    print(
        f"  {MA_SHORT}일선: {_won(r['ma20'])} ({'상승' if r['ma20_rising'] else '상승 아님'}) · "
        f"앵커드 VWAP(바닥부터): {_won(r['avwap'])}"
    )
    slope = "-" if r["ma60_slope_pct"] is None else f"{r['ma60_slope_pct']:+.1f}%"
    print(f"  {MA_MID}일선: {_won(r['ma60'])} · {MA_MID_SLOPE_DAYS}일 변화율 {slope} "
          f"({'상향 전환' if r['ma60_rising'] else '상향 전환 전'})")
    ev = r["evidence"]
    print(
        f"  보조 증거: 돌파 거래량 {_mark(ev['breakout_volume'])} · 바닥 거래량 {_mark(ev['base_volume'])}"
        f" · RSI 다이버전스 {_mark(ev['rsi_divergence'])} · Spring {_mark(ev['spring'])}"
    )
    if r["state"] == "basing" and r["missing"]:
        print(f"  고개 들기 미충족: {' · '.join(r['missing'])}")
    print("감시선 (종가 기준):")
    print(f"  - 무효화: {_won(r['invalidation'])} 종가 이탈 → 하락 중 재판정")
    if r["state"] == "basing" and r["breakout"] is not None:
        print(f"  - 고개 들기 확인: {_won(r['breakout'])} 종가 돌파 (+ 위 미충족 조건 해소)")
    if hl and r["state"] in ("turning", "extended"):
        print(f"  - 저점 높임 {_won(hl['price'])} 종가 이탈 → 바닥 다지기 복귀")
    if hl:
        print(f"  - 2차 증량: {_won(hl['price'])} 위 유지 + {MA_MID}일선 상향 전환 (현재 {slope})")
    print("  - 3차 증량: stage_scan 2단계 판정 또는 20주선 상승 전환")
    print(f"대응: {r['action']}")


# --- main ------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="단기 바닥 전환 판정 (「매매규칙 2」 초입)")
    parser.add_argument("query", help="종목명 또는 6자리 티커")
    parser.add_argument("--days", type=int, default=400, help="일봉 조회 일수 (기본 400 ≈ 13개월)")
    parser.add_argument("--json", action="store_true", help="판정 결과 JSON 덤프")
    parser.add_argument("--no-cache", action="store_true", help="캐시를 쓰지 않고 매번 새로 받는다 (캐시 오염 의심 시)")
    args = parser.parse_args(argv)
    if args.no_cache:
        http_cache.disable()

    # 티커 해석 경로는 하나다 — `context/ticker_overrides.md`가 검색 API보다 먼저다.
    stock, err, code = tickers.resolve_stock(args.query)
    if err:
        print(f"ERROR: {err}", file=sys.stderr)
        return code
    ticker = stock["stock_code"]
    name = stock.get("stock_name") or ticker

    bars, e = naver.fetch_bars(ticker, args.days)
    if e or not bars:
        print(f"ERROR: 시세 수집 실패 — {e or '빈 응답'} (네이버 siseJson, {ticker})", file=sys.stderr)
        return 1
    if len(bars) < MIN_BARS:
        print(
            f"ERROR: 일봉 {len(bars)}개 — 바닥 판정에 부족하다 (최소 {MIN_BARS}개 필요, --days 확대)",
            file=sys.stderr,
        )
        return 1

    result = analyze_turn(bars)
    if args.json:
        print(json.dumps(
            {"stock_code": ticker, "stock_name": name, "fetched_at": date.today().isoformat(),
             "source": "Naver siseJson", **result},
            ensure_ascii=False, default=str,
        ))
        return 0
    print_result(name, ticker, result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
