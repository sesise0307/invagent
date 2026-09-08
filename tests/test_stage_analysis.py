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


# --- 전망 (--project) -------------------------------------------------------


def test_price_for_cross_in_is_flat_on_a_flat_series() -> None:
    """모든 종가가 같으면 이평선도 같은 값이라 교차에 필요한 주가도 그 값이다."""
    closes = [100.0] * 300
    ma_now = stage_scan.sma(closes, stage_scan.MA_DAYS)[-1]

    for days in (20, 60, 100):
        assert stage_scan.price_for_cross_in(closes, ma_now, days) == pytest.approx(100.0)


def test_price_for_cross_in_matches_the_simulated_path() -> None:
    """닫힌 해는 경로 시뮬레이션과 같은 값을 줘야 한다."""
    closes = _wave(30_000, -0.2, 400)
    ma_now = stage_scan.sma(closes, stage_scan.MA_DAYS)[-1]
    days = 40

    needed = stage_scan.price_for_cross_in(closes, ma_now, days)
    path = stage_scan.project_ma_path(closes, ma_now, needed, days)

    assert path[-1] == pytest.approx(needed)


def test_price_for_cross_in_is_undefined_beyond_the_ma_window() -> None:
    closes = [100.0] * 300
    ma_now = stage_scan.sma(closes, stage_scan.MA_DAYS)[-1]

    assert stage_scan.price_for_cross_in(closes, ma_now, stage_scan.MA_DAYS) is None
    assert stage_scan.price_for_cross_in(closes, ma_now, 0) is None


def test_days_to_cross_counts_the_roll_off_of_old_bars() -> None:
    """150봉이 전부 200원이고 현재가가 100원이면 하루에 (100-200)/150씩 내려온다."""
    closes = [200.0] * stage_scan.MA_DAYS
    ma_now = 200.0

    assert stage_scan.days_to_cross(closes, ma_now, 100.0) == 150


def test_days_to_cross_handles_price_above_the_moving_average() -> None:
    closes = [100.0] * stage_scan.MA_DAYS
    ma_now = 100.0

    # 주가가 위에 있으면 이평선이 올라와 닿는 날을 센다
    assert stage_scan.days_to_cross(closes, ma_now, 200.0) == 150


def test_project_labels_the_direction_from_the_price_position() -> None:
    below = stage_scan.project(_bars(DOWNTREND))
    above = stage_scan.project(_bars(UPTREND))

    assert below["below"] is True and "아래에서" in below["direction"]
    assert above["below"] is False and "위에서" in above["direction"]
    assert below["targets"] and below["dropouts"]


def test_analyze_does_not_include_a_projection_by_default() -> None:
    """전망은 --project를 줬을 때만 붙는다. 기본 판정은 가정 없는 사실만 담는다."""
    assert "projection" not in _analyze(UPTREND)


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

def test_twenty_week_line_is_reported_for_the_stock_itself():
    """「기술적 분석 규칙 1」은 종목별 주봉 20주선을 요구하는데 아무도 계산하지 않았다.

    `advice` 룰 체크와 `analyze-stock` 10단계에 출력 슬롯은 있는데 그 숫자를 만드는 주체가
    없어 모델이 150일선으로 대신 읽거나 눈대중했다.
    """
    rising = [100.0 + i for i in range(400)]
    price = stage_scan.analyze(_bars(rising), None, "C")["price"]

    assert price["ma20w"] is not None
    assert price["ma20w_slope"] == "상승"
    assert price["ma20w_position"] in {"위", "아래", "오르내림"}
    # 20주선은 150일선과 다른 선이다 — 같은 값을 되풀이하면 룰이 요구한 축이 아니다.
    assert price["ma20w"] != price["ma"]


def test_twenty_week_line_is_a_documented_approximation():
    """일봉으로 주봉 20주선을 근사한다는 사실과 그 배수(5×20)가 문서에 남아야 한다."""
    assert stage_scan.MA_20WEEK == 100

    skill = (REPO_ROOT / ".agents" / "skills" / "stage-analysis" / "SKILL.md").read_text(encoding="utf-8")
    assert "MA_20WEEK" in skill
    assert "근사" in skill

def test_long_bull_candle_uses_the_threshold_from_my_rules():
    """「매매규칙 12」의 '장대 양봉'은 2026-09-05에 +8% 이상으로 정의됐다. 정의는 룰 파일이 정본이다."""
    import re

    rules = (REPO_ROOT / "context" / "my_rules.md").read_text(encoding="utf-8")
    m = re.search(r"장대 양봉[^.]*?(\d+)% 이상 상승", rules)
    assert m, "룰 12에 장대 양봉 정의가 없다"
    assert stage_scan.LONG_BULL_PCT == float(m.group(1))


def test_long_bull_candle_days_are_reported_with_their_close():
    """진입가를 장대 양봉 위에 잡지 않으려면 그 날짜와 종가를 알아야 한다."""
    closes = [100.0] * 200 + [100.0, 109.0, 110.0, 111.0]   # +9.0% 하루
    bars = _bars(closes)
    price = stage_scan.analyze(bars, None, "C")["price"]

    assert len(price["long_bull"]) == 1
    day = price["long_bull"][0]
    assert round(day["change"], 1) == 9.0
    assert day["close"] == 109.0
    assert day["date"]


def test_long_bull_candle_ignores_moves_below_the_threshold_and_drops():
    """+7.9%는 장대 양봉이 아니고, -9%는 양봉이 아니다."""
    price = stage_scan.analyze(_bars([100.0] * 200 + [100.0, 107.9, 98.0, 99.0]), None, "C")["price"]
    assert price["long_bull"] == []


def test_long_bull_candle_only_looks_at_the_recent_window():
    """반년 전 장대 양봉은 오늘의 진입가를 구속하지 않는다."""
    closes = [100.0, 109.0] + [109.0 + i * 0.01 for i in range(220)]
    price = stage_scan.analyze(_bars(closes), None, "C")["price"]
    assert price["long_bull"] == []


def test_equal_price_and_ma_is_neutral():
    result = _analyze([100.0] * 400)
    assert result["price"]["position"] == "오르내림"
    assert result["verdict"]["stage"] in (1, 3)


@pytest.mark.parametrize("close, expected", [(101, "위"), (99, "아래")])
def test_position_requires_eighty_percent_strict_residence(close, expected):
    assert stage_scan.price_vs_ma([close] * 16 + [100] * 4, [100] * 20)[0] == expected
    assert stage_scan.price_vs_ma([close] * 15 + [100] * 5, [100] * 20)[0] == "오르내림"


@pytest.mark.parametrize("highs, lows", [([100, 100], [90, 90]), ([100, 100], [90, 80])])
def test_equal_pivots_are_not_lower_highs_and_lows(highs, lows):
    assert stage_scan.swing_trend(
        [{"price": p} for p in highs], [{"price": p} for p in lows]
    ) == "혼조"


def test_equal_growth_is_flat_and_not_weak():
    financials = _financials([(2025, 2, 100), (2025, 3, 100), (2026, 2, 200)], [(2026, 3, 200)])
    growth = stage_scan.op_growth(financials, "C")
    assert growth["direction"] == "평탄"
    verdict = stage_scan.classify(_price("위", "상승", "높아짐", "상단", 12000), {}, growth, 12000)
    assert not verdict["weak"]
    assert verdict["confidence"] != "높음"
    assert not any("가격 전용" in reason for reason in verdict["reasons"])


@pytest.mark.parametrize("estimate", [[], [(2026, 4, 120)]])
def test_missing_immediate_next_quarter_cannot_raise_confidence(estimate):
    growth = stage_scan.op_growth(
        _financials([(2025, 2, 100), (2025, 4, 100), (2026, 2, 200)], estimate), "C"
    )
    assert not growth["available"]
    assert growth["next"] is None
    verdict = stage_scan.classify(_price("위", "상승", "높아짐", "상단", 12000), {}, growth, 12000)
    assert verdict["confidence"] != "높음"
    assert any("가격 전용" in reason for reason in verdict["reasons"])


def test_next_quarter_rolls_over_year():
    growth = stage_scan.op_growth(
        _financials([(2025, 1, 100), (2025, 4, 100), (2026, 4, 200)], [(2027, 1, 300)]), "C"
    )
    assert growth["next"]["period"] == "2027.1Q"


def test_twenty_week_slope_output_uses_one_week(capsys):
    stage_scan.print_result("test", "000000", _analyze(UPTREND), [])
    line = next(line for line in capsys.readouterr().out.splitlines() if "20주선(" in line)
    assert "/ 1주" in line
    assert "/ 20일" not in line


def test_projection_contact_is_not_a_stage_change(capsys):
    projection = stage_scan.project(_bars([100.0] * 400))
    assert projection["flat_days"] == 0
    assert projection["direction"] == "현재 이평선과 일치"
    stage_scan.print_projection(projection)
    output = capsys.readouterr().out
    assert "단계 전환을 뜻하지 않는다" in output
    assert "N거래일 뒤" in output
