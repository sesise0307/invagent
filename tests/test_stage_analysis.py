"""`stage-analysis` 스킬의 단계 판정 로직 검증.

네트워크를 타지 않는다. 합성 시계열과 합성 재무 payload만 쓴다.
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / ".agents" / "skills" / "stage-analysis" / "scripts" / "stage_scan.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("stage_scan", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stage_scan = _load_module()


def _bars(closes: list[float], start_index: int = 0) -> list[dict]:
    """종가 리스트를 일봉으로 부풀린다. 고가·저가는 종가 ±1%."""
    return [
        {
            "date": f"2026{(i % 12) + 1:02d}{(i % 28) + 1:02d}_{i:04d}",
            "open": c,
            "high": c * 1.01,
            "low": c * 0.99,
            "close": c,
            "volume": 1_000_000 + (i % 7) * 10_000,
        }
        for i, c in enumerate(closes, start=start_index)
    ]


def _wave(base: float, drift: float, n: int, amplitude: float = 0.03) -> list[float]:
    """추세(drift, 봉당 %)에 사인 파동을 얹은 종가열."""
    return [
        base * (1 + drift / 100) ** i * (1 + amplitude * math.sin(i / 9))
        for i in range(n)
    ]


UPTREND = _wave(10_000, 0.25, 400)
DOWNTREND = _wave(40_000, -0.25, 400)
# 상승 후 고점 횡보 → 3단계 / 하락 후 저점 횡보 → 1단계
TOP_FLAT = _wave(10_000, 0.25, 250) + _wave(10_000 * 1.0025**249, 0.0, 150)
BOTTOM_FLAT = _wave(40_000, -0.25, 250) + _wave(40_000 * 0.9975**249, 0.0, 150)


def _analyze(closes: list[float], financials=None, primary: str = "C") -> dict:
    return stage_scan.analyze(_bars(closes), financials, primary)


# --- 가격 축 기본 4단계 -----------------------------------------------------


def test_uptrend_is_stage_two() -> None:
    result = _analyze(UPTREND)
    assert result["verdict"]["stage"] == 2
    assert result["price"]["position"] == "위"
    assert result["price"]["slope"] == "상승"


def test_downtrend_is_stage_four() -> None:
    result = _analyze(DOWNTREND)
    assert result["verdict"]["stage"] == 4
    assert result["price"]["position"] == "아래"
    assert result["price"]["slope"] == "하락"


def test_flat_after_rally_is_stage_three() -> None:
    result = _analyze(TOP_FLAT)
    assert result["verdict"]["stage"] == 3
    assert result["price"]["slope"] == "평탄"


def test_flat_after_decline_is_stage_one() -> None:
    result = _analyze(BOTTOM_FLAT)
    assert result["verdict"]["stage"] == 1
    assert result["price"]["slope"] == "평탄"


# --- 경계 규칙 (리포트 p.20~21) --------------------------------------------


def _price(position: str, slope: str, swing: str, band: str, close: float) -> dict:
    return {
        "position": position,
        "slope": slope,
        "swing": swing,
        "band": band,
        "long_trend_up": band != "하단",
        "close": close,
    }


def _growth(direction: str = "판정 불가", available: bool = False) -> dict:
    return {"available": available, "direction": direction, "latest": None, "next": None, "note": None}


def test_stage_three_returns_to_two_when_prior_cyclical_high_is_broken() -> None:
    price = _price("오르내림", "평탄", "혼조", "상단", 12_000)
    cyclical = {"high": {"price": 11_000, "date": "20260601"}, "low": {"price": 8_000, "date": "20250901"}}
    verdict = stage_scan.classify(price, cyclical, _growth(), 12_000)

    assert verdict["stage"] == 2
    assert any("순환적 고점" in r for r in verdict["reasons"])


def test_stage_one_returns_to_four_when_prior_cyclical_low_is_broken() -> None:
    price = _price("오르내림", "평탄", "혼조", "하단", 7_500)
    cyclical = {"high": {"price": 11_000, "date": "20260601"}, "low": {"price": 8_000, "date": "20250901"}}
    verdict = stage_scan.classify(price, cyclical, _growth(), 7_500)

    assert verdict["stage"] == 4
    assert any("순환적 저점" in r for r in verdict["reasons"])


# --- 이평선 쌍 우선 규칙 ----------------------------------------------------


def test_above_rising_ma_is_stage_two_even_when_swing_disagrees() -> None:
    """리포트의 3단계는 '이평선을 오르내리고 이평선이 평탄한' 상태다.

    주가가 150일선 위에 머물고 이평선까지 상승 중이면 스윙이 낮아져도 2단계다.
    다만 어긋남을 근거에 남기고 확신도를 높음으로 올리지 않는다.
    """
    price = _price("위", "상승", "낮아짐", "상단", 12_000)
    verdict = stage_scan.classify(price, {}, _growth("상승", available=True), 12_000)

    assert verdict["stage"] == 2
    assert verdict["confidence"] != "높음"
    assert any("어긋난다" in r for r in verdict["reasons"])


def test_below_falling_ma_is_stage_four_even_when_swing_disagrees() -> None:
    price = _price("아래", "하락", "높아짐", "하단", 7_000)
    verdict = stage_scan.classify(price, {}, _growth("하락", available=True), 7_000)

    assert verdict["stage"] == 4
    assert any("어긋난다" in r for r in verdict["reasons"])


def test_stage_two_warning_separates_late_stage_from_early_stage() -> None:
    """기울기가 어긋난 방향이 후반부와 초입부를 가른다.

    평탄 = 상승세가 식은 2단계 후반, 하락 = 바닥에서 갓 올라탄 2단계 초입.
    두 경우를 같은 문구로 묶으면 매수·매도 방향이 반대로 읽힌다.
    """
    flat = stage_scan.classify(
        _price("위", "평탄", "혼조", "상단", 12_000), {}, _growth(), 12_000
    )
    falling = stage_scan.classify(
        _price("위", "하락", "혼조", "중단", 12_000), {}, _growth(), 12_000
    )

    assert any("2단계 후반" in r for r in flat["reasons"])
    assert not any("초입" in r for r in flat["reasons"])
    assert any("2단계 초입" in r for r in falling["reasons"])
    assert not any("후반이거나 3단계" in r for r in falling["reasons"])


def test_stage_four_warning_separates_late_stage_from_early_stage() -> None:
    flat = stage_scan.classify(
        _price("아래", "평탄", "혼조", "하단", 7_000), {}, _growth(), 7_000
    )
    rising = stage_scan.classify(
        _price("아래", "상승", "혼조", "중단", 7_000), {}, _growth(), 7_000
    )

    assert any("4단계 후반" in r for r in flat["reasons"])
    assert any("4단계 초입" in r for r in rising["reasons"])


def test_above_flat_ma_is_stage_two_not_stage_one() -> None:
    """주가가 150일선 위에 붙어 있으면 1·3단계 후보가 아니다.

    1·3단계는 주가가 이평선을 오르내리는 상태다 (리포트 p.5·p.8). 이평선이 평탄해도
    주가가 위에 체류하면 2단계이며, 기울기 불일치는 경고로만 남는다.
    """
    price = _price("위", "평탄", "혼조", "중단", 12_000)
    verdict = stage_scan.classify(price, {}, _growth(), 12_000)

    assert verdict["stage"] == 2
    assert any("기울기 평탄" in r for r in verdict["reasons"])


def test_below_flat_ma_is_stage_four_not_stage_three() -> None:
    price = _price("아래", "평탄", "혼조", "중단", 7_000)
    verdict = stage_scan.classify(price, {}, _growth(), 7_000)

    assert verdict["stage"] == 4
    assert any("기울기 평탄" in r for r in verdict["reasons"])


# --- 영업이익 축: 약한 2단계 / 약한 4단계 -----------------------------------


def test_price_stage_two_with_falling_op_growth_is_weak_stage_two() -> None:
    price = _price("위", "상승", "높아짐", "상단", 12_000)
    verdict = stage_scan.classify(price, {}, _growth("하락", available=True), 12_000)

    assert verdict["stage"] == 2
    assert verdict["weak"] is True
    assert verdict["label"].startswith("약한 2단계")


def test_price_stage_four_with_rising_op_growth_is_weak_stage_four() -> None:
    price = _price("아래", "하락", "낮아짐", "하단", 7_000)
    verdict = stage_scan.classify(price, {}, _growth("상승", available=True), 7_000)

    assert verdict["stage"] == 4
    assert verdict["weak"] is True
    assert verdict["label"].startswith("약한 4단계")


def test_missing_op_growth_degrades_to_price_only_without_error() -> None:
    result = _analyze(UPTREND, financials=None)

    assert result["verdict"]["stage"] == 2
    assert result["verdict"]["weak"] is False
    assert any("가격 전용 판정" in r for r in result["verdict"]["reasons"])
    assert result["growth"]["available"] is False


# --- 영업이익 증가율 계산 ---------------------------------------------------


def _financials(actual: list[tuple[int, int, float]], estimate: list[tuple[int, int, float]]) -> dict:
    def rows(items):
        return [{"year": y, "quarter": q, "operating_income": v} for y, q, v in items]

    return {"consolidated": rows(actual), "consolidatedEstimate": rows(estimate)}


def test_op_growth_compares_latest_actual_with_next_estimate() -> None:
    # 2026.2Q 확정 +100% vs 2026.3Q 추정 +20% → 하락 (리포트 p.23 계산법)
    fin = _financials(
        actual=[(2025, 2, 100.0), (2025, 3, 100.0), (2026, 1, 90.0), (2026, 2, 200.0)],
        estimate=[(2026, 2, 190.0), (2026, 3, 120.0)],
    )
    growth = stage_scan.op_growth(fin, "C")

    assert growth["available"] is True
    assert growth["latest"]["period"] == "2026.2Q"
    assert growth["next"]["period"] == "2026.3Q"
    assert growth["latest"]["yoy"] == pytest.approx(100.0)
    assert growth["next"]["yoy"] == pytest.approx(20.0)
    assert growth["direction"] == "하락"


def test_op_growth_is_unavailable_when_prior_year_was_a_loss() -> None:
    fin = _financials(
        actual=[(2025, 2, -50.0), (2026, 2, 200.0)],
        estimate=[(2026, 3, 120.0)],
    )
    growth = stage_scan.op_growth(fin, "C")

    assert growth["available"] is False
    assert growth["direction"] == "판정 불가"
    assert "0 이하" in (growth["note"] or "")


# --- siseJson 파싱 ---------------------------------------------------------


SISE_SAMPLE = """[['날짜', '시가', '고가', '저가', '종가', '거래량', '외국인소진율'],
["20260803", 248000, 249500, 238000, 239500, 27825493, 46.61],
["20260804", 244500, 244500, 228000, 240000, 29433821, 46.63]]"""


def test_parse_sise_drops_header_and_sorts_ascending() -> None:
    bars = stage_scan.parse_sise(SISE_SAMPLE)

    assert [b["date"] for b in bars] == ["20260803", "20260804"]
    assert bars[0]["close"] == 239500
    assert bars[1]["volume"] == 29433821


def test_analyze_reports_the_moving_average_pair() -> None:
    result = _analyze(UPTREND)

    assert result["price"]["ma"] is not None
    assert result["price"]["ma_ref"] is not None
    assert result["price"]["close"] > result["price"]["ma"]
