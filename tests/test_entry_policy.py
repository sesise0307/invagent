"""analyze-stock `entry_policy.py` — 단계·전환·밸류 게이트별 진입 경로."""

from __future__ import annotations

import pytest

from tests.conftest import load_script


def _entry_payload(**overrides) -> dict:
    """진입 게이트가 전부 통과하는 기본 입력. 시험할 게이트만 덮어쓴다."""
    payload = {
        "asof": "2026-09-09",
        "valuation_grade": "buy_candidate",
        "account_pause": {
            "status": "ok",
            "active": False,
            "remaining_trading_days": 0,
            "review_complete": True,
        },
        "stage": {"status": "ok", "value": 2},
        "turn": {"status": "ok", "value": "turning"},
        "overhang": {"status": "ok", "value": "none"},
        "event": {"status": "ok", "enabled": False},
        "long_bull": {"status": "ok", "active": False},
        "earnings": {"status": "ok", "surprise": False},
        "instrument": {"status": "ok", "kind": "stock", "leveraged": False},
    }
    payload.update(overrides)
    return payload


def test_entry_policy_stage_two_stays_eligible() -> None:
    """가드 — 밸류 경로를 여는 동안 2단계 정상 진입이 깨지면 안 된다."""
    module = load_script("analyze-stock", "entry_policy")

    result = module.decide(_entry_payload())

    assert result["action"] == "eligible"
    assert result["max_tranche_fraction"] is None


def test_entry_policy_leaves_overhang_out_of_the_decision() -> None:
    """오버행은 §6-A 참고 사항이다 — 미해소·미수집이어도 action과 1차 수량 상한을 바꾸지 않는다."""
    module = load_script("analyze-stock", "entry_policy")

    unresolved = module.decide(_entry_payload(overhang={"status": "ok", "value": "unresolved"}))
    unknown = module.decide(_entry_payload(overhang={"status": "missing"}))
    absent = _entry_payload()
    del absent["overhang"]

    for result in (unresolved, unknown, module.decide(absent)):
        assert result["action"] == "eligible"
        assert result["max_tranche_fraction"] is None
        assert "overhang" not in result["checks"]


def test_entry_policy_withholds_when_stage_is_unknown() -> None:
    """가드 — 알 수 없음은 허가로 승격되지 않는다."""
    module = load_script("analyze-stock", "entry_policy")

    result = module.decide(_entry_payload(stage={"status": "missing"}))

    assert result["action"] == "withhold"


VALUE_GATE_PASS = {
    "status": "ok",
    "downside_blocked": True,
    "reward_risk": 3.4,
    "first_tranche_fraction": 0.03,
    "target_weight_fraction": 0.10,
    "next_tranche_trigger": "직전 순환적 저점 회복 후 스윙 고점 돌파",
}


def test_entry_policy_opens_stage_one_for_a_passing_value_gate() -> None:
    """1단계는 매집 구간이다. 하방이 막힌 저평가면 1차 분할이 열려야 한다."""
    module = load_script("analyze-stock", "entry_policy")

    result = module.decide(
        _entry_payload(stage={"status": "ok", "value": 1}, value_gate=dict(VALUE_GATE_PASS))
    )

    assert result["action"] == "watch"
    assert result["max_tranche_fraction"] == pytest.approx(0.03)


def test_entry_policy_withholds_stage_one_without_a_value_gate() -> None:
    """밸류 근거 없이 1단계에 들어가는 것은 여전히 막는다 — 진입 수량 0."""
    module = load_script("analyze-stock", "entry_policy")

    result = module.decide(_entry_payload(stage={"status": "ok", "value": 1}))

    assert result["action"] == "withhold"
    assert result["max_tranche_fraction"] is None


def test_entry_policy_treats_stage_three_like_stage_one() -> None:
    """3단계도 밸류 게이트 통과 시 1차 분할만 예외로 연다. 미통과면 수량 0."""
    module = load_script("analyze-stock", "entry_policy")

    opened = module.decide(
        _entry_payload(stage={"status": "ok", "value": 3}, value_gate=dict(VALUE_GATE_PASS))
    )
    closed = module.decide(_entry_payload(stage={"status": "ok", "value": 3}))

    assert opened["action"] == "watch"
    assert opened["max_tranche_fraction"] == pytest.approx(0.03)
    assert closed["action"] == "withhold"


def test_entry_policy_opens_stage_four_only_once_the_stock_turns_up() -> None:
    """가치주 바닥은 150일선상 4단계인 채로 온다. 단기로 고개를 들었으면 '하락하는 와중'이 아니다.

    2026-09-12 사용자 확정 — 4단계도 「고개 들기」 + 밸류 게이트면 1차 분할이 열린다.
    바닥을 다지는 중이거나 밸류 게이트가 없으면 여전히 닫혀 있다.
    """
    module = load_script("analyze-stock", "entry_policy")
    stage4 = {"status": "ok", "value": 4}

    turned = module.decide(_entry_payload(stage=stage4, value_gate=dict(VALUE_GATE_PASS)))
    basing = module.decide(
        _entry_payload(stage=stage4, value_gate=dict(VALUE_GATE_PASS),
                       turn={"status": "ok", "value": "basing"})
    )
    no_gate = module.decide(_entry_payload(stage=stage4))

    assert turned["action"] == "watch"
    assert turned["max_tranche_fraction"] == pytest.approx(0.03)
    assert basing["action"] == "withhold"
    assert basing["max_tranche_fraction"] is None
    assert no_gate["action"] == "withhold"


def test_entry_policy_waits_for_the_turn_before_the_value_path_opens() -> None:
    """「매매규칙 2」는 바닥을 다진 '후' 고개 드는 초반에 산다 — 다지는 중이나 초입을 지난 뒤는 수량 0."""
    module = load_script("analyze-stock", "entry_policy")

    for value in ("basing", "extended"):
        result = module.decide(
            _entry_payload(stage={"status": "ok", "value": 1}, value_gate=dict(VALUE_GATE_PASS),
                           turn={"status": "ok", "value": value})
        )
        assert result["action"] == "withhold", value
        assert result["max_tranche_fraction"] is None, value


ACCUMULATION_PASS = {"status": "ok", "eligible": True, "op_yoy_increased": True}


def test_entry_policy_opens_half_a_first_tranche_before_the_turn_for_a_growing_quiet_base() -> None:
    """경로 C — 「매매규칙 2」 단서. 바닥 30일 + 바닥 +10% 이내 + 영업이익 YoY 증가 + 밸류 게이트면 고개 들기 전에
    1차 트랜치의 절반을 연다. 4단계도 같다 (2026-09-25 백테스트, 사용자 확정)."""
    module = load_script("analyze-stock", "entry_policy")

    for stage in (1, 3, 4):
        result = module.decide(
            _entry_payload(stage={"status": "ok", "value": stage}, value_gate=dict(VALUE_GATE_PASS),
                           turn={"status": "ok", "value": "basing"}, accumulation=dict(ACCUMULATION_PASS))
        )
        assert result["action"] == "watch", stage
        assert result["max_tranche_fraction"] == pytest.approx(0.015), stage
        assert "매매규칙 2" in result["checks"]["stage"]["rule"]


def test_entry_policy_keeps_the_base_closed_when_any_accumulation_condition_is_missing() -> None:
    """실적 감소, 가격 조건 미충족, 판정 못 받음, 밸류 게이트 없음 — 하나라도 빠지면 고개 들기를 기다린다."""
    module = load_script("analyze-stock", "entry_policy")
    basing = {"status": "ok", "value": "basing"}
    cases = {
        "영업이익 감소": dict(value_gate=dict(VALUE_GATE_PASS), accumulation={**ACCUMULATION_PASS, "op_yoy_increased": False}),
        "실적 모름": dict(value_gate=dict(VALUE_GATE_PASS), accumulation={**ACCUMULATION_PASS, "op_yoy_increased": None}),
        "가격 조건 미충족": dict(value_gate=dict(VALUE_GATE_PASS), accumulation={**ACCUMULATION_PASS, "eligible": False}),
        "판정 없음": dict(value_gate=dict(VALUE_GATE_PASS), accumulation={"status": "missing"}),
        "밸류 게이트 없음": dict(accumulation=dict(ACCUMULATION_PASS)),
    }
    for label, extra in cases.items():
        result = module.decide(_entry_payload(stage={"status": "ok", "value": 4}, turn=basing, **extra))
        assert result["action"] == "withhold", label
        assert result["max_tranche_fraction"] is None, label


def test_entry_policy_never_accumulates_into_a_stock_that_is_still_falling() -> None:
    module = load_script("analyze-stock", "entry_policy")

    result = module.decide(
        _entry_payload(stage={"status": "ok", "value": 4}, value_gate=dict(VALUE_GATE_PASS),
                       turn={"status": "ok", "value": "falling"}, accumulation=dict(ACCUMULATION_PASS))
    )

    assert result["action"] == "avoid"


def test_entry_policy_avoids_a_stock_that_is_still_falling() -> None:
    """방금 신저가를 쓴 종목은 밸류가 아무리 좋아도 하락하는 와중이다."""
    module = load_script("analyze-stock", "entry_policy")

    result = module.decide(
        _entry_payload(stage={"status": "ok", "value": 1}, value_gate=dict(VALUE_GATE_PASS),
                       turn={"status": "ok", "value": "falling"})
    )

    assert result["action"] == "avoid"
    assert result["max_tranche_fraction"] is None


def test_entry_policy_withholds_the_value_path_when_the_turn_is_unknown() -> None:
    """가드 — 단기 판정을 못 받았으면 허가로 승격하지 않는다."""
    module = load_script("analyze-stock", "entry_policy")

    missing = _entry_payload(stage={"status": "ok", "value": 3}, value_gate=dict(VALUE_GATE_PASS))
    del missing["turn"]
    errored = _entry_payload(stage={"status": "ok", "value": 3}, value_gate=dict(VALUE_GATE_PASS),
                             turn={"status": "error"})

    for payload in (missing, errored):
        result = module.decide(payload)
        assert result["action"] == "withhold"
        assert result["max_tranche_fraction"] is None


def test_entry_policy_stage_two_does_not_consult_the_turn() -> None:
    """2단계는 경로 A다 — 단기 판정은 1·3·4단계의 타이밍 게이트일 뿐 주도주 진입을 막지 않는다."""
    module = load_script("analyze-stock", "entry_policy")

    payload = _entry_payload(turn={"status": "ok", "value": "extended"})
    result = module.decide(payload)

    assert result["action"] == "eligible"
    assert result["max_tranche_fraction"] is None


def test_value_gate_requires_a_blocked_downside() -> None:
    """하방 막힘이 필수 조건이다. 기대수익만 큰 종목으로 경로가 열리면 안 된다."""
    module = load_script("analyze-stock", "entry_policy")

    result = module.decide(
        _entry_payload(
            stage={"status": "ok", "value": 1},
            value_gate={**VALUE_GATE_PASS, "downside_blocked": False},
        )
    )

    assert result["action"] == "withhold"
    assert result["max_tranche_fraction"] is None


def test_value_gate_requires_a_higher_reward_risk_than_the_general_rule() -> None:
    """추세 확인 없이 들어가는 대가로 손익비 문턱이 일반 2.0보다 높다.

    문턱은 2.5다 (사용자 확정 2026-09-22). 3.0은 15% 분모와 맞물리면 중심
    기대수익 45%를 요구해 「기본 원칙 2」 본문의 50%와 겹치고, 20% 분모 시절에는
    60%를 요구해 룰 원문보다 엄격했다.
    """
    module = load_script("analyze-stock", "entry_policy")

    assert module.VALUE_GATE_MIN_REWARD_RISK == pytest.approx(2.5)
    below = module.decide(
        _entry_payload(
            stage={"status": "ok", "value": 1},
            value_gate={**VALUE_GATE_PASS, "reward_risk": 2.49},
        )
    )
    at_threshold = module.decide(
        _entry_payload(
            stage={"status": "ok", "value": 1},
            value_gate={**VALUE_GATE_PASS, "reward_risk": module.VALUE_GATE_MIN_REWARD_RISK},
        )
    )

    assert below["action"] == "withhold"
    assert at_threshold["action"] == "watch"


def test_value_gate_caps_the_first_tranche_at_a_third_of_target_weight() -> None:
    """경로 B는 1차 분할 한정이다. 목표 비중을 통째로 싣는 tranche는 통과시키지 않는다."""
    module = load_script("analyze-stock", "entry_policy")

    oversized = module.decide(
        _entry_payload(
            stage={"status": "ok", "value": 1},
            value_gate={**VALUE_GATE_PASS, "first_tranche_fraction": 0.05},
        )
    )
    at_limit = module.decide(
        _entry_payload(
            stage={"status": "ok", "value": 1},
            value_gate={
                **VALUE_GATE_PASS,
                "first_tranche_fraction": 0.10 * module.VALUE_PATH_TRANCHE_RATIO,
            },
        )
    )

    assert oversized["action"] == "withhold"
    assert at_limit["action"] == "watch"


def test_value_gate_requires_a_next_tranche_trigger() -> None:
    """나머지 tranche를 무엇이 여는지 안 적었으면 1차도 열지 않는다 — 「매매규칙 4」 사전 계획."""
    module = load_script("analyze-stock", "entry_policy")

    blank = module.decide(
        _entry_payload(
            stage={"status": "ok", "value": 1},
            value_gate={**VALUE_GATE_PASS, "next_tranche_trigger": "   "},
        )
    )
    missing = module.decide(
        _entry_payload(
            stage={"status": "ok", "value": 1},
            value_gate={k: v for k, v in VALUE_GATE_PASS.items() if k != "next_tranche_trigger"},
        )
    )

    assert blank["action"] == "withhold"
    assert missing["action"] == "withhold"
