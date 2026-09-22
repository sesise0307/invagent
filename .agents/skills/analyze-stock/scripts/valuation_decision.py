#!/usr/bin/env python3
"""Compute the documented valuation range and decision from JSON input."""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path
from typing import Any

MIN_STOP_LOSS = 0.20
# 손익비 분모의 하한. 실행 손절선(MIN_STOP_LOSS)과 일부러 분리해 둔다 — 20%는
# 「매매규칙 6」의 전량 이탈선이라 1회 최대 손실을 정하고 「기본 원칙 4(2%룰)」의
# 분모로 남아야 하지만, 손익비 분모까지 거기 묶으면 손익비가 `중심 기대수익 ÷ 20%`
# 로 붕괴해 기대수익의 재진술이 된다. 하방이 막힌 종목이 분모를 낮게 받도록 15%를
# 쓴다 (사용자 확정 2026-09-22). 두 값을 다시 합치지 마라.
MIN_REWARD_RISK_STOP = 0.15
MIN_REWARD_RISK = 2.0
ACCOUNT_RISK_LIMIT = 0.02
GRADES = ("avoid", "watch", "buy_candidate")


class DecisionInputError(ValueError):
    """Raised when valuation inputs would make a decision unsafe or ambiguous."""


def _number(value: Any, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise DecisionInputError(f"{field} must be a finite number")
    value = float(value)
    if positive and value <= 0:
        raise DecisionInputError(f"{field} must be greater than zero")
    return value


def _scenario(payload: dict[str, Any]) -> tuple[dict[str, float], dict[str, float], float]:
    targets = {name: _number(payload.get(name), f"scenarios.{name}", positive=True)
               for name in ("bear", "base", "bull")}
    if not targets["bear"] <= targets["base"] <= targets["bull"]:
        raise DecisionInputError("scenario targets must satisfy bear <= base <= bull")
    raw = payload.get("probabilities")
    if not isinstance(raw, dict):
        raise DecisionInputError("scenarios.probabilities must be an object")
    probs = {name: _number(raw.get(name), f"scenarios.probabilities.{name}")
             for name in ("bear", "base", "bull")}
    if any(value < 0 for value in probs.values()):
        raise DecisionInputError("scenario probabilities cannot be negative")
    total = sum(probs.values())
    if math.isclose(total, 100.0, abs_tol=1e-9):
        probs = {key: value / 100 for key, value in probs.items()}
    elif not math.isclose(total, 1.0, abs_tol=1e-9):
        raise DecisionInputError("scenario probabilities must sum to 1 or 100")
    expected = sum(probs[name] * targets[name] for name in targets)
    return targets, probs, expected


def _consensus(payload: dict[str, Any]) -> dict[str, Any]:
    values = {name: _number(payload.get(name), f"consensus.{name}", positive=True)
              for name in ("low", "average", "high")}
    if not values["low"] <= values["average"] <= values["high"]:
        raise DecisionInputError("consensus targets must satisfy low <= average <= high")
    coverage = payload.get("coverage")
    if isinstance(coverage, bool) or not isinstance(coverage, int) or coverage < 1:
        raise DecisionInputError("consensus.coverage must be a positive integer")
    latest = payload.get("latest_report_date")
    if latest is not None:
        try:
            date.fromisoformat(latest)
        except (TypeError, ValueError) as exc:
            raise DecisionInputError("consensus.latest_report_date must be an ISO date") from exc
    recent = payload.get("recent_report_count", 0)
    if isinstance(recent, bool) or not isinstance(recent, int) or recent < 0:
        raise DecisionInputError("consensus.recent_report_count must be a non-negative integer")
    revision = payload.get("eps_revision", "unknown")
    if revision not in {"persistent_down", "down", "flat", "up", "unknown"}:
        raise DecisionInputError("consensus.eps_revision has an unsupported value")
    return {**values, "coverage": coverage, "latest_report_date": latest,
            "recent_report_count": recent, "eps_revision": revision}


def _weight(consensus: dict[str, Any], asof: date) -> tuple[float, str, list[dict[str, Any]]]:
    dispersion = consensus["high"] / consensus["low"]
    stale = False
    age_days = None
    if consensus["latest_report_date"]:
        latest = date.fromisoformat(consensus["latest_report_date"])
        if latest > asof:
            raise DecisionInputError("consensus.latest_report_date cannot be after asof")
        age_days = (asof - latest).days
        stale = age_days > 183
    if dispersion > 2:
        weight, rule = 0.3, "1: consensus dispersion > 2.0"
    elif consensus["coverage"] <= 2 or stale:
        weight, rule = 0.3, "2: coverage <= 2 or latest report older than 6 months"
    elif consensus["coverage"] >= 8 and consensus["recent_report_count"] >= 3:
        weight, rule = 0.65, "3: coverage >= 8 and >= 3 reports in 3 months"
    else:
        weight, rule = 0.5, "4: default"
    audit = [{"operation": "consensus_weight_rule", "result": weight, "reason": rule,
              "dispersion": dispersion, "latest_report_age_days": age_days}]
    if consensus["eps_revision"] == "persistent_down":
        before = weight
        weight = max(0.2, weight - 0.1)
        audit.append({"operation": "persistent_eps_down_adjustment", "before": before,
                      "change": -0.1, "floor": 0.2, "result": weight})
        rule += "; persistent EPS downgrade -0.1"
    return weight, rule, audit


def _downgrade(grade: str) -> str:
    return GRADES[max(0, GRADES.index(grade) - 1)]


def _perspective_state(raw: Any) -> tuple[str, int]:
    values = list(raw.values()) if isinstance(raw, dict) else raw
    if not isinstance(values, list) or len(values) != 3:
        return "missing", 1
    normalized = [str(value).lower() for value in values]
    if any(value in {"", "unknown", "missing", "미확인", "미정"} for value in normalized):
        return "missing", 1
    if len(set(normalized)) == 1:
        return "aligned", 0
    if len(set(normalized)) == 3:
        return "split", 2
    return "one_opposed", 1


def _confidence(width: float, consensus: dict[str, Any] | None,
                scenarios: dict[str, Any] | None, payload: dict[str, Any]) -> tuple[str, list[str]]:
    collection = payload.get("collection_steps") or {}
    missing_steps = sum(collection.get(key) not in {"ok", "collected"} for key in ("step5", "step6"))
    perspective, perspective_defects = _perspective_state(payload.get("perspectives"))
    coverage = consensus["coverage"] if consensus else 0
    defects = missing_steps + (1 if coverage < 5 else 0) + perspective_defects
    reasons = [f"range width {width:.4f}", f"missing collection steps {missing_steps}",
               f"coverage {coverage}", f"perspectives {perspective}"]
    if width > 1 or consensus is None or scenarios is None or defects >= 2 or perspective == "split":
        return "low", reasons
    if width <= 0.5 and missing_steps == 0 and coverage >= 5 and perspective == "aligned":
        return "high", reasons
    return "medium", reasons


def decide(payload: Any) -> dict[str, Any]:
    """Return valuation decision with every material numeric intermediate."""
    if not isinstance(payload, dict):
        raise DecisionInputError("input must be an object")
    current = _number(payload.get("current_price"), "current_price", positive=True)
    try:
        asof = date.fromisoformat(payload.get("asof", date.today().isoformat()))
    except (TypeError, ValueError) as exc:
        raise DecisionInputError("asof must be an ISO date") from exc
    consensus = _consensus(payload["consensus"]) if payload.get("consensus") is not None else None
    scenario_targets = probabilities = None
    scenario_expected = None
    if payload.get("scenarios") is not None:
        if not isinstance(payload["scenarios"], dict):
            raise DecisionInputError("scenarios must be an object or null")
        scenario_targets, probabilities, scenario_expected = _scenario(payload["scenarios"])
    if consensus is None and scenario_targets is None:
        raise DecisionInputError("consensus and scenarios cannot both be missing")

    audit: list[dict[str, Any]] = []
    disjoint = bool(consensus and scenario_targets and
                    (consensus["high"] < scenario_targets["bear"] or
                     scenario_targets["bull"] < consensus["low"]))
    selection = payload.get("source_selection")
    weight_reason = "single available source"
    if disjoint:
        if not isinstance(selection, dict) or selection.get("source") not in {"consensus", "scenarios"}:
            raise DecisionInputError(
                "disjoint ranges require source_selection.source of consensus or scenarios"
            )
        if not isinstance(selection.get("reason"), str) or not selection["reason"].strip():
            raise DecisionInputError("disjoint ranges require source_selection.reason")
        selected = selection["source"]
        if selected == "consensus":
            low, center, high = consensus["low"], consensus["average"], consensus["high"]
            consensus_weight = 1.0
        else:
            low, center, high = scenario_targets["bear"], scenario_expected, scenario_targets["bull"]
            consensus_weight = 0.0
        weight_reason = f"blend prohibited; selected {selected}: {selection['reason'].strip()}"
        audit.append({"operation": "disjoint_source_selection", "selected": selected,
                      "reason": selection["reason"], "result": [low, center, high]})
    elif consensus is None:
        consensus_weight = 0.0
        low, center, high = scenario_targets["bear"], scenario_expected, scenario_targets["bull"]
        audit.append({"operation": "scenario_only", "result": [low, center, high]})
    elif scenario_targets is None:
        consensus_weight = 1.0
        low, center, high = consensus["low"], consensus["average"], consensus["high"]
        audit.append({"operation": "consensus_only", "result": [low, center, high]})
    else:
        consensus_weight, weight_reason, weight_audit = _weight(consensus, asof)
        audit.extend(weight_audit)
        scenario_weight = 1 - consensus_weight
        low = consensus_weight * consensus["low"] + scenario_weight * scenario_targets["bear"]
        center = consensus_weight * consensus["average"] + scenario_weight * scenario_expected
        high = consensus_weight * consensus["high"] + scenario_weight * scenario_targets["bull"]
        audit.append({"operation": "three_point_blend", "consensus_weight": consensus_weight,
                      "scenario_weight": scenario_weight, "result": [low, center, high]})
    if not low <= center <= high or center <= 0:
        raise DecisionInputError("computed range must satisfy low <= center <= high and center > 0")

    returns = {key: value / current - 1 for key, value in
               (("low", low), ("center", center), ("high", high))}
    downside = (current - low) / current
    effective_stop = max(downside, MIN_STOP_LOSS)
    reward_risk_stop = max(downside, MIN_REWARD_RISK_STOP)
    reward_risk = returns["center"] / reward_risk_stop
    if returns["center"] >= 0.50:
        grade, rule = "buy_candidate", "center return >= 50%"
    elif returns["center"] >= 0.30 and downside <= 0:
        grade, rule = "buy_candidate", "center return >= 30% and scenario downside is blocked"
    elif returns["center"] >= 0.25:
        grade, rule = "watch", "center return >= 25%"
    else:
        grade, rule = "avoid", "center return < 25%"
    base_grade = grade
    downgrades: list[str] = []
    if reward_risk < MIN_REWARD_RISK:
        grade = _downgrade(grade)
        downgrades.append("reward/risk < 2.0")
    if returns["low"] < -0.30:
        grade = _downgrade(grade)
        downgrades.append("low-end expected return < -30%")
    position_cap = ACCOUNT_RISK_LIMIT / effective_stop
    entry_price = _number(payload.get("entry_price", current), "entry_price", positive=True)
    account_value = payload.get("account_value")
    max_amount = max_shares = None
    if account_value is not None:
        account_value = _number(account_value, "account_value", positive=True)
        max_amount = account_value * position_cap
        max_shares = math.floor(max_amount / entry_price)
    width = (high - low) / center
    confidence, confidence_reasons = _confidence(width, consensus, scenario_targets, payload)
    audit.append({"operation": "risk", "downside": downside, "stop_floor": MIN_STOP_LOSS,
                  "reward_risk_stop": reward_risk_stop,
                  "effective_stop": effective_stop, "center_return": returns["center"],
                  "reward_risk": reward_risk, "position_cap": position_cap})

    return {
        "asof": asof.isoformat(),
        "range": {"low": low, "center": center, "high": high},
        "expected_returns": returns,
        "weights": {"consensus": consensus_weight, "scenarios": 1 - consensus_weight,
                    "reason": weight_reason},
        "dispersion": {
            "range_width": width,
            "consensus": consensus["high"] / consensus["low"] if consensus else None,
            "scenarios": scenario_targets["bull"] / scenario_targets["bear"]
            if scenario_targets else None,
        },
        "probabilities": probabilities,
        "scenario_expected_value": scenario_expected,
        "blend_prohibited": disjoint,
        "decision": {"base_grade": base_grade, "grade": grade, "base_rule": rule,
                     "downgrades": downgrades, "confidence": confidence,
                     "confidence_reasons": confidence_reasons},
        "risk": {"downside": downside, "effective_stop_loss": effective_stop,
                 "reward_risk_denominator": reward_risk_stop,
                 "reward_risk": reward_risk, "account_risk_limit": ACCOUNT_RISK_LIMIT,
                 "max_purchase_fraction": position_cap, "entry_price": entry_price,
                 "stop_prices": {"first_partial": entry_price * 0.85,
                                 "full_exit": entry_price * 0.80},
                 "account_value": account_value, "max_purchase_amount": max_amount,
                 "max_whole_shares": max_shares},
        "audit": audit,
    }


def _read_json(path: str | None) -> Any:
    text = Path(path).read_text(encoding="utf-8") if path else sys.stdin.read()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise DecisionInputError(f"invalid JSON: {exc.msg}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute valuation decision from JSON")
    parser.add_argument("input", nargs="?", help="JSON file; stdin when omitted")
    args = parser.parse_args(argv)
    try:
        result = decide(_read_json(args.input))
    except (DecisionInputError, OSError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
