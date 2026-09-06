#!/usr/bin/env python3
"""현금 투입 사다리 채점기.

2026-09-03에 확정한 3분할 현금 투입 조건이 오늘 충족됐는지 기계적으로 채점한다.
사다리 자체는 `output/daily-digest/monthly_context.md`「매크로 흐름」에 등재돼 있고,
이 스크립트는 그 조건표의 집행부다. 판정은 스크립트가 확정하고, 모델은 해석만 한다.

데이터 출처
  - 지수 일봉(150일선·20주선·연속 유지일): 네이버 `api.finance.naver.com/siseJson.naver`
    (`invagent.datafeed.naver.fetch_bars` 재사용, 인증 불필요)
  - 분산일·랠리일차·FTD·200일선 하회 비율: StockEasy `stockdata/api/v1/market/*`
    (`fetch_market_signals.fetch_api` 재사용, 인증 불필요)
  - VKOSPI·수급 주체별 순매수: 자동 수집 경로가 없어 `--vkospi`·`--net-buy-days`로 받는다.
    안 주면 해당 조건은 ❓(미확인)이고, ❓는 충족으로 승격되지 않는다.

임계값은 이 스킬의 운영 선택이며 근거는 SKILL.md 1-1-1단계에 있다. 둘은 함께 고친다.
"""

import argparse
import sys
from datetime import date
from pathlib import Path

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from fetch_market_signals import fetch_api  # noqa: E402  (같은 스킬 폴더)

from invagent.datafeed import cache as http_cache
from invagent.datafeed.naver import fetch_bars
from invagent.datafeed.series import sma

# --- 사다리 상수 (2026-09-03 확정) -------------------------------------------
# 사다리를 세운 날. FTD는 이 날짜보다 뒤에 찍힌 것만 새 신호로 인정한다.
LADDER_START = "2026-09-03"
# 무효화선 = 2026-07-30 KOSPI 종가(이번 사이클 저점). 이탈하면 사다리 전체를 접는다.
INVALIDATION_CLOSE = 5593.56
# 추세 판정선. 150일선 = Weinstein 축, 100일선 = 20주선의 일봉 근사(5거래일 × 20주).
MA_TREND = 150
MA_20WEEK = 100
# 1차 조건: 150일선 위에서 며칠을 버텨야 「복귀」로 보는가.
HOLD_DAYS = 3
# 1차 조건: 활성 분산일 상한. 오닐 기준 4~5개면 조정, 2개 이하를 소진으로 본다.
MAX_DISTRIBUTION_DAYS = 2
# 2차 조건: 200일선 하회 종목 비율 상한. 2026-09-03 기준 80.4%.
MAX_BELOW_200MA_RATIO = 0.60
# 2차 조건: VKOSPI 상한. 40 이상이 공포권이므로 30을 정상권 복귀선으로 쓴다.
MAX_VKOSPI = 30.0
# 2차 조건: 이 날짜를 지나야 9월 FOMC 리스크가 소멸한다.
FOMC_DATE = date(2026, 9, 17)
# 3차 조건: 외국인·기관 중 한 주체의 연속 순매수 일수 하한.
MIN_NET_BUY_DAYS = 5

TRANCHES = {1: 50_000_000, 2: 70_000_000, 3: 50_000_000}
RESERVE = 34_000_000

OK, NG, UNKNOWN = "✅", "❌", "❓"


class Condition:
    """채점 문항 하나. state는 OK/NG/UNKNOWN 중 하나."""

    def __init__(self, code: str, label: str, state: str, detail: str):
        self.code, self.label, self.state, self.detail = code, label, state, detail

    def __repr__(self) -> str:  # 테스트 실패 메시지용
        return f"<{self.code} {self.state} {self.detail}>"


def flag(passed: bool) -> str:
    return OK if passed else NG


# --- 지표 계산 (순수 함수) ---------------------------------------------------


def hold_days_above(closes: list[float], line: list[float | None]) -> int:
    """종가가 이동평균 위에 연속으로 머문 거래일 수(오늘부터 역산)."""
    n = 0
    for c, m in zip(reversed(closes), reversed(line)):
        if m is None or c <= m:
            break
        n += 1
    return n


def grade(metrics: dict, today: date) -> list[Condition]:
    """수집된 지표를 사다리 조건표로 채점한다. 네트워크를 타지 않는 순수 함수."""
    c: list[Condition] = []

    ftd = metrics.get("last_ftd")
    c.append(
        Condition(
            "C1",
            f"KOSPI 신규 FTD 성립 ({LADDER_START} 이후)",
            flag(bool(ftd) and ftd > LADDER_START),
            f"최근 FTD {ftd or '없음'}",
        )
    )

    dd = metrics.get("distribution_days")
    c.append(
        Condition(
            "C2",
            f"KOSPI 활성 분산일 {MAX_DISTRIBUTION_DAYS}개 이하",
            UNKNOWN if dd is None else flag(dd <= MAX_DISTRIBUTION_DAYS),
            "미확인" if dd is None else f"{dd}개",
        )
    )

    hold = metrics.get("hold_days_ma150")
    ma150 = metrics.get("ma150")
    close = metrics.get("close")
    c.append(
        Condition(
            "C3",
            f"KOSPI 150일선 위 {HOLD_DAYS}거래일 유지",
            UNKNOWN if hold is None else flag(hold >= HOLD_DAYS),
            "미확인"
            if hold is None
            else f"{hold}일 (종가 {close:,.2f} / MA150 {ma150:,.2f}, {(close / ma150 - 1) * 100:+.2f}%)",
        )
    )

    c.append(
        Condition(
            "C4",
            f"9월 FOMC({FOMC_DATE}) 통과",
            flag(today > FOMC_DATE),
            f"오늘 {today}",
        )
    )

    below = metrics.get("below_200ma_ratio")
    c.append(
        Condition(
            "C5",
            f"200일선 하회 비율 {MAX_BELOW_200MA_RATIO:.0%} 미만",
            UNKNOWN if below is None else flag(below < MAX_BELOW_200MA_RATIO),
            "미확인" if below is None else f"{below:.1%}",
        )
    )

    vk = metrics.get("vkospi")
    c.append(
        Condition(
            "C6",
            f"VKOSPI {MAX_VKOSPI:.0f} 이하",
            UNKNOWN if vk is None else flag(vk <= MAX_VKOSPI),
            "미확인 (--vkospi 로 입력)" if vk is None else f"{vk:.2f}",
        )
    )

    ma20w = metrics.get("ma20week")
    c.append(
        Condition(
            "C7",
            "KOSPI 20주선(≈100일선) 회복",
            UNKNOWN if ma20w is None else flag(close >= ma20w),
            "미확인"
            if ma20w is None
            else f"종가 {close:,.2f} / 20주선 {ma20w:,.2f} ({(close / ma20w - 1) * 100:+.2f}%)",
        )
    )

    nb = metrics.get("net_buy_days")
    c.append(
        Condition(
            "C8",
            f"외국인·기관 중 1주체 {MIN_NET_BUY_DAYS}거래일 누적 순매수",
            UNKNOWN if nb is None else flag(nb >= MIN_NET_BUY_DAYS),
            "미확인 (--net-buy-days 로 입력)" if nb is None else f"{nb}일",
        )
    )

    return c


def verdict(conditions: list[Condition], close: float | None) -> tuple[int, str]:
    """충족 단계와 사유를 낸다. ❓는 충족으로 승격하지 않는다."""
    if close is not None and close < INVALIDATION_CLOSE:
        return -1, f"무효화 — KOSPI {close:,.2f} < 사이클 저점 {INVALIDATION_CLOSE:,.2f}"

    state = {c.code: c.state for c in conditions}
    gates = {1: ("C1", "C2", "C3"), 2: ("C4", "C5", "C6"), 3: ("C7", "C8")}

    reached = 0
    for step in (1, 2, 3):
        codes = gates[step]
        if all(state[k] == OK for k in codes):
            reached = step
            continue
        missing = [k for k in codes if state[k] != OK]
        return reached, f"{step}차 미충족 — {', '.join(missing)}"
    return 3, "3단계 전부 충족"


# --- 수집 ------------------------------------------------------------------


def collect(vkospi: float | None, net_buy_days: int | None) -> tuple[dict, list[str]]:
    """지표를 모은다. 실패한 항목은 None으로 남기고 사유를 함께 반환한다."""
    metrics: dict = {"vkospi": vkospi, "net_buy_days": net_buy_days}
    notes: list[str] = []

    bars, err = fetch_bars("KOSPI", 700)
    if err or not bars:
        notes.append(f"KOSPI 일봉 실패 ({err or '빈 응답'}) — C3·C7 미확인")
    else:
        closes = [b["close"] for b in bars]
        ma150 = sma(closes, MA_TREND)
        ma20w = sma(closes, MA_20WEEK)
        metrics.update(
            close=closes[-1],
            asof=bars[-1]["date"],
            ma150=ma150[-1],
            ma20week=ma20w[-1],
            hold_days_ma150=hold_days_above(closes, ma150),
        )

    bp, err = fetch_api("big_picture")
    if err or not bp:
        notes.append(f"big-picture 실패 ({err or '빈 응답'}) — C1·C2 미확인")
    else:
        k = bp.get("kospi") or {}
        metrics["last_ftd"] = k.get("last_ftd_date")
        metrics["distribution_days"] = len(
            [x for x in k.get("distribution_days", []) if x.get("is_active")]
        )
        metrics["rally_day_count"] = k.get("rally_day_count")

    mm, err = fetch_api("market_monitor")
    if err or not mm:
        notes.append(f"market-monitor 실패 ({err or '빈 응답'}) — C5 미확인")
    else:
        rows = mm.get("data") or []
        if rows:
            metrics["below_200ma_ratio"] = rows[-1].get("200down_ratio")

    return metrics, notes


def main() -> int:
    p = argparse.ArgumentParser(description="현금 투입 사다리 채점 (2026-09-03 확정 조건)")
    p.add_argument("--vkospi", type=float, help="오늘 VKOSPI 종가 (1-2단계에서 수집한 값)")
    p.add_argument("--net-buy-days", type=int, help="외국인·기관 중 최대 연속 순매수 일수")
    p.add_argument(
        "--no-cache", action="store_true", help="캐시를 쓰지 않고 매번 새로 받는다 (캐시 오염 의심 시)"
    )
    args = p.parse_args()
    if args.no_cache:
        http_cache.disable()

    metrics, notes = collect(args.vkospi, args.net_buy_days)
    conditions = grade(metrics, date.today())
    step, reason = verdict(conditions, metrics.get("close"))

    asof = metrics.get("asof", "?")
    print(f"=== 현금 투입 사다리 채점 (기준일 {asof}) ===")
    for c in conditions:
        print(f"  {c.state} {c.code} {c.label} — {c.detail}")

    print()
    if step < 0:
        print(f"[판정] ⛔ {reason}")
        print("       신규 매수 중단, 「기본 원칙 13」 절차로 전환.")
    elif step == 0:
        print(f"[판정] 대기 — {reason}. 투입 금액 ₩0")
    else:
        amount = sum(TRANCHES[i] for i in range(1, step + 1))
        print(f"[판정] {step}차까지 개방 — 누적 투입 한도 ₩{amount:,} ({reason})")
    print(f"       상시 유보 ₩{RESERVE:,}는 어느 단계에서도 쓰지 않는다.")

    for n in notes:
        print(f"[누락] {n}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
