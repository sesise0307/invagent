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
    """손익비 분모(15%)와 2%룰 분모(15%)는 숫자가 같아도 다른 것을 잰다.

    2%룰 하한은 「매매규칙 6」의 전량 이탈선이라 1회 최대 손실을 정하고, 그래서
    「기본 원칙 4(2%룰)」의 분모로 남아야 한다. 손익비는 시나리오 하방 대비
    보상을 재는 값이므로 실행 손절선에 묶이면 기대수익의 재진술이 된다.
    """
    module = load_script("analyze-stock", "valuation_decision")

    result = module.decide(_downside_blocked_payload())

    center_return = result["expected_returns"]["center"]
    risk = result["risk"]
    assert risk["downside"] < 0, "이 입력은 하방이 막혀 있어야 한다"
    assert risk["reward_risk_denominator"] == pytest.approx(module.MIN_REWARD_RISK_STOP)
    assert module.MIN_REWARD_RISK_STOP == pytest.approx(0.15)
    assert risk["reward_risk"] == pytest.approx(center_return / module.MIN_REWARD_RISK_STOP)
    # 2%룰과 실행 손절선은 「매매규칙 6」 전량 이탈선 15%에 묶여 있다 (2026-10-21 개정).
    assert risk["effective_stop_loss"] == pytest.approx(0.15)
    assert risk["max_purchase_fraction"] == pytest.approx(0.02 / 0.15)


def test_stop_prices_follow_the_two_stop_tiers() -> None:
    """「매매규칙 6」은 -12% 1차 분할, -15% 전량이다 — 진입가 10,000원이면 8,800원 · 8,500원."""
    module = load_script("analyze-stock", "valuation_decision")
    payload = _downside_blocked_payload()
    payload["entry_price"] = 10000.0

    stops = module.decide(payload)["risk"]["stop_prices"]

    assert stops["first_partial"] == pytest.approx(8800.0)
    assert stops["full_exit"] == pytest.approx(8500.0)
