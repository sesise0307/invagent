"""analyze-stock `valuation_decision.py` — 목표가 범위와 손익비."""

from __future__ import annotations

import pytest

from tests.conftest import load_script


def _downside_blocked_payload() -> dict:
    """하단 목표가가 현재가보다 높은(하방 막힌) 입력. 두 분모의 차이가 드러나는 자리다."""
    return {
        "asof": "2026-09-22",
        "current_price": 100.0,
        "consensus": {
            "low": 110.0, "average": 140.0, "high": 180.0,
            "coverage": 10, "latest_report_date": "2026-09-11",
            "recent_report_count": 5, "eps_revision": "flat",
        },
        "scenarios": {
            "bear": 100.0, "base": 140.0, "bull": 190.0,
            "probabilities": {"bear": 20, "base": 60, "bull": 20},
        },
    }


def test_reward_risk_denominator_is_separate_from_the_position_sizing_floor() -> None:
    """손익비 분모(15%)와 2%룰 분모(12%)는 다른 것을 잰다.

    2%룰 하한은 「매매 규칙 6」 세 티어의 가중 손실(30% × 8% + 30% × 12% + 40% × 15% = 12%)이다
    (「매매 규칙 9」 2026-11-11 개정) — 티어대로 팔았을 때 포지션이 실제로 내는 손실이다.
    손익비는 시나리오 하방 대비 보상을 재는 값이므로 실행 손절선에 묶이면 기대수익의 재진술이 된다.
    """
    module = load_script("analyze-stock", "valuation_decision")

    result = module.decide(_downside_blocked_payload())

    center_return = result["expected_returns"]["center"]
    risk = result["risk"]
    assert risk["downside"] < 0, "이 입력은 하방이 막혀 있어야 한다"
    assert risk["reward_risk_denominator"] == pytest.approx(module.MIN_REWARD_RISK_STOP)
    assert module.MIN_REWARD_RISK_STOP == pytest.approx(0.15)
    assert risk["reward_risk"] == pytest.approx(center_return / module.MIN_REWARD_RISK_STOP)
    # 2%룰 분모는 티어 가중 손실 12%다 — 매수원금 상한은 계좌의 1/6(약 16.7%).
    assert risk["effective_stop_loss"] == pytest.approx(0.12)
    assert risk["max_purchase_fraction"] == pytest.approx(1 / 6)
    # 전량 이탈선 가격은 여전히 -15%다.
    assert risk["stop_prices"]["full_exit"] == pytest.approx(risk["entry_price"] * 0.85)


def test_stop_prices_follow_the_three_stop_tiers() -> None:
    """「매매 규칙 6」의 -8%·-12%·-15% 선이다 — 진입가 10,000원이면 9,200원 · 8,800원 · 8,500원."""
    module = load_script("analyze-stock", "valuation_decision")
    payload = _downside_blocked_payload()
    payload["entry_price"] = 10000.0

    stops = module.decide(payload)["risk"]["stop_prices"]

    assert stops["first_trim"] == pytest.approx(9200.0)
    assert stops["second_trim"] == pytest.approx(8800.0)
    assert stops["full_exit"] == pytest.approx(8500.0)


def _thirty_percent_center_payload(**extra) -> dict:
    """중심 수익률 40%, 하단목표가 95 < 현재가 100 — 하단목표가로는 하방이 안 막힌다."""
    return {
        "asof": "2026-09-22",
        "current_price": 100.0,
        "scenarios": {
            "bear": 95.0, "base": 140.0, "bull": 185.0,
            "probabilities": {"bear": 0, "base": 100, "bull": 0},
        },
        **extra,
    }


def test_center_thirty_without_blocked_downside_is_only_watch() -> None:
    module = load_script("analyze-stock", "valuation_decision")

    result = module.decide(_thirty_percent_center_payload())

    assert result["risk"]["downside_blocked"] is False
    assert result["decision"]["base_grade"] == "watch"


def test_value_band_floor_blocks_downside_for_the_grade() -> None:
    """「하방이 막힌」 정의(2026-11-01): 밸류 밴드 최하단이면 하단목표가와 무관하게 막힌 것이다."""
    module = load_script("analyze-stock", "valuation_decision")

    result = module.decide(_thirty_percent_center_payload(at_value_band_floor=True))

    assert result["risk"]["downside_blocked"] is True
    assert result["decision"]["base_grade"] == "buy_candidate"
    # 손익비와 2%룰 분모는 여전히 실측 하방으로 계산한다.
    assert result["risk"]["downside"] == pytest.approx(0.05)
    assert result["risk"]["effective_stop_loss"] == pytest.approx(0.12)


def test_value_band_floor_must_be_a_boolean() -> None:
    module = load_script("analyze-stock", "valuation_decision")

    with pytest.raises(module.DecisionInputError):
        module.decide(_thirty_percent_center_payload(at_value_band_floor="yes"))
