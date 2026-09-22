#!/usr/bin/env python3
"""Combine investment gates into an advisory action without executing trades.

Example input::

  {"asof":"2026-09-06","valuation_grade":"buy_candidate",
   "account_pause":{"status":"ok","active":false,"remaining_trading_days":0,
                    "review_complete":true},
   "stage":{"status":"ok","value":2},
   "event":{"status":"ok","enabled":false},
   "long_bull":{"status":"ok","active":false},
   "earnings":{"status":"ok","surprise":false},
   "instrument":{"status":"ok","kind":"stock","leveraged":false}}

Stages 1, 3 and 4 open only when ``turn`` (turn_scan.py's short-term bottom state) is
``turning`` and ``value_gate`` (가치 우선 진입) passes, which caps the entry at the planned
first tranche. ``falling`` avoids; ``basing``, ``extended`` or an unknown turn withhold::

  {"stage":{"status":"ok","value":4},
   "turn":{"status":"ok","value":"turning"},
   "value_gate":{"status":"ok","downside_blocked":true,"reward_risk":3.4,
                 "first_tranche_fraction":0.03,"target_weight_fraction":0.10,
                 "next_tranche_trigger":"저점 높임 유지 + 60일선 상향 전환"}}

Stage 2 is the trend-confirmed path and does not consult ``turn``.

Overhang (§4-A) is a reference note in the report, not a gate: an ``overhang`` key
is accepted and ignored, so it never moves ``action`` or ``max_tranche_fraction``.

Fractions use 0..1 (5% is 0.05). Output is policy advice only.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path
from typing import Any

EVENT_LIMIT = 0.05
# 경로 B의 손익비 문턱. 일반 판정(2.0)보다 높되 「기본 원칙 2」 본문(50%)보다
# 엄격하지는 않게 둔다 — 3.0은 valuation_decision의 15% 분모와 맞물리면 중심
# 기대수익 45%, 이전 20% 분모에서는 60%를 요구해 룰 원문을 넘어섰다
# (사용자 확정 2026-09-22). 바꾸려면 analyze-stock/SKILL.md 밸류 게이트도 같이.
VALUE_GATE_MIN_REWARD_RISK = 2.5
VALUE_PATH_TRANCHE_RATIO = 1 / 3
ACTION_PRIORITY = {"eligible": 0, "watch": 1, "withhold": 2, "avoid": 3}
# turn_scan.py의 `state` 값. 고개 들기(turning)만 경로 B를 연다.
TURN_STATES = {
    "falling": "still falling",
    "basing": "still basing — wait for the turn above the breakout close",
    "turning": "turning up",
    "extended": "past the early stage — wait for a pullback",
}


class EntryPolicyError(ValueError):
    """Raised for malformed or contradictory entry-policy input."""


def _fraction(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise EntryPolicyError(f"{field} must be a finite fraction")
    value = float(value)
    if not 0 <= value <= 1:
        raise EntryPolicyError(f"{field} must be between 0 and 1")
    return value


def _state(payload: dict[str, Any], name: str) -> dict[str, Any] | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise EntryPolicyError(f"{name} must be an object")
    if value.get("status") not in {"ok", "unknown", "missing", "error", "stale"}:
        raise EntryPolicyError(f"{name}.status is invalid")
    return value


def _value_first_tranche(gate: dict[str, Any] | None) -> float | None:
    """가치 우선 진입의 1차 수량. 게이트가 못 열면 None — 진입 수량 0이다."""
    if not gate or gate["status"] != "ok":
        return None
    blocked = gate.get("downside_blocked")
    if not isinstance(blocked, bool):
        raise EntryPolicyError("value_gate.downside_blocked must be boolean")
    if not blocked:
        return None
    reward_risk = gate.get("reward_risk")
    if isinstance(reward_risk, bool) or not isinstance(reward_risk, (int, float)) \
            or not math.isfinite(reward_risk):
        raise EntryPolicyError("value_gate.reward_risk must be a finite number")
    if reward_risk < VALUE_GATE_MIN_REWARD_RISK:
        return None
    trigger = gate.get("next_tranche_trigger")
    if not isinstance(trigger, str) or not trigger.strip():
        return None
    tranche = _fraction(gate.get("first_tranche_fraction"), "value_gate.first_tranche_fraction")
    target = _fraction(gate.get("target_weight_fraction"), "value_gate.target_weight_fraction")
    if tranche <= 0 or tranche > target * VALUE_PATH_TRANCHE_RATIO + 1e-12:
        return None
    return tranche


def decide(payload: Any) -> dict[str, Any]:
    """Evaluate critical gates; unknown evidence never becomes permission."""
    if not isinstance(payload, dict):
        raise EntryPolicyError("input must be an object")
    try:
        asof = date.fromisoformat(payload.get("asof"))
    except (TypeError, ValueError) as exc:
        raise EntryPolicyError("asof must be an ISO date") from exc

    grade = payload.get("valuation_grade")
    if grade not in {"buy_candidate", "watch", "avoid"}:
        raise EntryPolicyError("valuation_grade must be buy_candidate, watch, or avoid")
    action = {"buy_candidate": "eligible", "watch": "watch", "avoid": "avoid"}[grade]
    reasons: list[dict[str, str]] = []
    checks: dict[str, dict[str, Any]] = {}
    cap = 1.0

    def apply(name: str, outcome: str, reason: str, rule: str) -> None:
        nonlocal action
        checks[name] = {"outcome": outcome, "reason": reason, "rule": rule}
        reasons.append({"gate": name, "outcome": outcome, "reason": reason, "rule": rule})
        if ACTION_PRIORITY[outcome] > ACTION_PRIORITY[action]:
            action = outcome

    account = _state(payload, "account_pause")
    if not account or account["status"] != "ok":
        apply("account_pause", "withhold", "account pause/review state is unknown",
              "기본 원칙 13(계좌 MDD 관리)")
    else:
        active = account.get("active")
        remaining = account.get("remaining_trading_days")
        review = account.get("review_complete")
        if not isinstance(active, bool) or isinstance(remaining, bool) or not isinstance(remaining, int) \
                or remaining < 0 or not isinstance(review, bool):
            raise EntryPolicyError("account_pause requires active, non-negative remaining_trading_days, and review_complete")
        if active or remaining > 0 or not review:
            apply("account_pause", "watch", "new-buy pause or required review is incomplete",
                  "기본 원칙 13(계좌 MDD 관리)")
        else:
            apply("account_pause", "eligible", "pause cleared and review complete",
                  "기본 원칙 13(계좌 MDD 관리)")

    stage = _state(payload, "stage")
    if not stage or stage["status"] != "ok":
        apply("stage", "withhold", "stage evidence is unknown", "stage-analysis gate")
    else:
        value = stage.get("value")
        if value not in {1, 2, 3, 4}:
            raise EntryPolicyError("stage.value must be 1, 2, 3, or 4")
        if value == 2:
            apply("stage", "eligible", "stage 2 is the buy-stage gate", "매매규칙 2(펀더 기반 매수)")
        else:
            # 1·3·4단계는 단기 바닥 전환 판정(turn_scan)이 타이밍 게이트다. 고개를 든 뒤에만
            # 밸류 게이트가 1차 분할을 연다 — 4단계도 같다 (2026-09-12 사용자 확정).
            turn = _state(payload, "turn")
            turn_value = turn.get("value") if turn and turn["status"] == "ok" else None
            if turn_value is not None and turn_value not in TURN_STATES:
                raise EntryPolicyError("turn.value must be falling, basing, turning, or extended")
            if turn_value is None:
                apply("stage", "withhold", f"stage {value} with unknown short-term turn evidence",
                      "매매규칙 2(펀더 기반 매수)")
            elif turn_value == "falling":
                apply("stage", "avoid", f"stage {value} and still falling — no buying into a decline",
                      "매매규칙 2(펀더 기반 매수)")
            elif turn_value != "turning":
                apply("stage", "withhold", f"stage {value} {TURN_STATES[turn_value]}",
                      "매매규칙 2(펀더 기반 매수)")
            else:
                tranche = _value_first_tranche(_state(payload, "value_gate"))
                if tranche is None:
                    apply("stage", "withhold", f"stage {value} turning without a passing value gate",
                          "매매규칙 2(펀더 기반 매수)")
                else:
                    cap = min(cap, tranche)
                    apply("stage", "watch",
                          f"stage {value} turning, value-first entry: capped first tranche only",
                          "매매규칙 4(분할 매수)")

    long_bull = _state(payload, "long_bull")
    if not long_bull or long_bull["status"] != "ok":
        apply("long_bull", "withhold", "long-bull-day evidence is unknown", "매매규칙 12(뇌동매매 금지)")
    elif not isinstance(long_bull.get("active"), bool):
        raise EntryPolicyError("long_bull.active must be boolean")
    elif long_bull["active"]:
        apply("long_bull", "watch", "entry on a >=8% long bullish day is prohibited",
              "매매규칙 12(뇌동매매 금지)")
    else:
        apply("long_bull", "eligible", "no active long-bull entry restriction",
              "매매규칙 12(뇌동매매 금지)")

    earnings = _state(payload, "earnings")
    if not earnings or earnings["status"] != "ok":
        apply("earnings", "withhold", "earnings-event evidence is unknown",
              "매매규칙 13(실적 발표 직후 매수 금지)")
    elif not isinstance(earnings.get("surprise"), bool):
        raise EntryPolicyError("earnings.surprise must be boolean")
    elif earnings["surprise"] and not earnings.get("cause_analyzed", False):
        apply("earnings", "watch", "surprise cause has not been analyzed",
              "매매규칙 13(실적 발표 직후 매수 금지)")
    else:
        apply("earnings", "eligible", "no unanalyzed earnings surprise",
              "매매규칙 13(실적 발표 직후 매수 금지)")

    event = _state(payload, "event")
    if not event or event["status"] != "ok":
        apply("event", "withhold", "event-trade evidence is unknown", "매매규칙 14(이벤트 매매 제한)")
    elif not isinstance(event.get("enabled"), bool):
        raise EntryPolicyError("event.enabled must be boolean")
    elif event["enabled"]:
        leader = event.get("sector_leader")
        if not isinstance(leader, bool):
            apply("event", "withhold", "sector-leader status is unknown", "매매규칙 14(이벤트 매매 제한)")
        elif not leader:
            apply("event", "avoid", "event trade is limited to a clear sector leader",
                  "매매규칙 14(이벤트 매매 제한)")
        else:
            current_event = _fraction(event.get("current_purchase_fraction"),
                                      "event.current_purchase_fraction")
            proposed_event = _fraction(event.get("proposed_purchase_fraction"),
                                       "event.proposed_purchase_fraction")
            plan = event.get("plan")
            needed = {"date", "expected_scenario", "profit_taking_schedule", "thesis_break"}
            if not isinstance(plan, dict) or any(not plan.get(key) for key in needed):
                apply("event", "withhold", "required event plan is incomplete",
                      "매매규칙 14(이벤트 매매 제한)")
            elif current_event + proposed_event > EVENT_LIMIT + 1e-12:
                apply("event", "avoid", "event purchase principal would exceed 5%",
                      "매매규칙 14(이벤트 매매 제한)")
            else:
                cap = min(cap, EVENT_LIMIT - current_event)
                apply("event", "eligible", "event plan and 5% cap pass",
                      "매매규칙 14(이벤트 매매 제한)")
    else:
        apply("event", "eligible", "not an event-based trade", "매매규칙 14(이벤트 매매 제한)")

    instrument = _state(payload, "instrument")
    if not instrument or instrument["status"] != "ok":
        apply("leverage", "withhold", "instrument/leverage state is unknown", "레버리지 규칙 1~3")
    else:
        kind, leveraged = instrument.get("kind"), instrument.get("leveraged")
        if kind not in {"stock", "index", "sector"} or not isinstance(leveraged, bool):
            raise EntryPolicyError("instrument requires kind stock/index/sector and leveraged boolean")
        if not leveraged:
            apply("leverage", "eligible", "instrument is not leveraged", "레버리지 규칙 1~3")
        elif kind == "stock":
            apply("leverage", "avoid", "individual-stock leverage is prohibited", "레버리지 규칙 1")
        else:
            leverage = _state(payload, "leverage")
            if not leverage or leverage["status"] != "ok":
                apply("leverage", "withhold", "leverage volatility state is unknown", "레버리지 규칙 2~3")
            else:
                volatility = leverage.get("volatility_rule")
                if volatility not in {"active", "inactive"}:
                    raise EntryPolicyError("leverage.volatility_rule must be active or inactive")
                if volatility == "inactive" or leverage.get("consistent_upward_exception") is True:
                    apply("leverage", "eligible", "leverage liquidation rule is inactive or upward exception applies",
                          "레버리지 규칙 2~3")
                elif kind == "sector":
                    apply("leverage", "avoid", "climax exception does not apply to sector leverage",
                          "레버리지 규칙 2~3")
                else:
                    climax = leverage.get("climax_exception")
                    if not isinstance(climax, dict) or climax.get("status") != "ok":
                        apply("leverage", "withhold", "index climax exception is unknown",
                              "레버리지 규칙 2")
                    elif climax.get("qualified") is True:
                        tranche = _fraction(climax.get("first_tranche_fraction"),
                                            "leverage.climax_exception.first_tranche_fraction")
                        if tranche <= 0:
                            raise EntryPolicyError("climax first_tranche_fraction must be greater than zero")
                        cap = min(cap, tranche)
                        apply("leverage", "eligible", "qualified index climax permits a capped new tranche",
                              "레버리지 규칙 2")
                    elif climax.get("qualified") is False:
                        apply("leverage", "avoid", "active liquidation rule and no market exception",
                              "레버리지 규칙 3")
                    else:
                        raise EntryPolicyError("climax_exception.qualified must be boolean")

    return {
        "asof": asof.isoformat(),
        "grade": grade,
        "action": action,
        "max_tranche_fraction": cap if cap < 1 else None,
        "checks": checks,
        "reasons": reasons,
        "execution": {"performed": False, "note": "advisory output; no financial action was executed"},
    }


def _read_json(path: str | None) -> Any:
    text = Path(path).read_text(encoding="utf-8") if path else sys.stdin.read()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise EntryPolicyError(f"invalid JSON: {exc.msg}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Combine entry gates from JSON (all fractions use 0..1)",
        epilog="See module docstring for a complete example JSON object.",
    )
    parser.add_argument("input", nargs="?", help="JSON file; stdin when omitted")
    args = parser.parse_args(argv)
    try:
        result = decide(_read_json(args.input))
    except (EntryPolicyError, OSError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
