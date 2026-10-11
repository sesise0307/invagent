#!/usr/bin/env python3
"""10단계 진입 판정 — 스캔·밸류 결과를 `entry_policy.decide`의 입력으로 엮는다.

모델이 stage_scan·turn_scan·valuation_decision 출력을 손으로 옮겨 적던 번역(경로 A/B/C를 가르는
자리)을 코드로 옮긴 것이다.

    uv run python .agents/skills/analyze-stock/scripts/entry_decision.py <티커> \\
        --valuation <valuation.json> --context <context.json> [--asof YYYY-MM-DD] [--no-cache]

- `stage`·`turn`·`accumulation`: `stage-analysis` 스킬의 `stage_scan.py`·`turn_scan.py`를 **경로로
  실행**해(`--json`) 받는다. 다른 스킬 스크립트를 import하지 않는다 (AGENTS.md 배치 규칙).
  자식이 실패하면(비정상 종료·JSON 아님) 그 키는 `{"status":"unknown","reason":…}`이고
  `decide()`가 보류한다.
- `valuation_grade`·`value_gate`: `--valuation`(valuation_decision.py 입력 JSON)을 같은 폴더의
  `valuation_decision.decide`로 계산한다. 하방 막힘 = `risk.downside <= 0`(하단목표가 ≥ 현재가),
  손익비 = `risk.reward_risk`. 1차 수량·목표 비중·증량 트리거는 context의 `tranche_plan`이다.
- `account_pause`·`event`·`long_bull`·`earnings`·`instrument`(필수), `leverage`·`tranche_plan`·`asof`
  (선택)는 `--context` JSON을 그대로 넘긴다. 모르는 키나 빠진 필수 키는 exit 2.

출력: `decide()` 결과 + 조립한 입력 전체(`inputs`). 종료 코드 0 판정 완료(보류 포함) · 2 입력 오류.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import entry_policy  # noqa: E402  (같은 스크립트 폴더)
import valuation_decision  # noqa: E402  (같은 스크립트 폴더)

STAGE_SCRIPTS = SCRIPTS_DIR.parents[1] / "stage-analysis" / "scripts"
STAGE_SCAN = STAGE_SCRIPTS / "stage_scan.py"
TURN_SCAN = STAGE_SCRIPTS / "turn_scan.py"
REQUIRED_CONTEXT = ("account_pause", "event", "long_bull", "earnings", "instrument")
OPTIONAL_CONTEXT = ("asof", "leverage", "tranche_plan")
TRANCHE_PLAN_KEYS = ("first_tranche_fraction", "target_weight_fraction", "next_tranche_trigger")
REASON_MAX_CHARS = 500


class EntryDecisionError(ValueError):
    """Raised for malformed composer input (context or valuation JSON)."""


def run_child(script: Path, args: list[str]) -> tuple[int, str, str]:
    """자식 스크립트를 이 인터프리터로 실행해 (종료 코드, stdout, stderr)를 돌려준다."""
    proc = subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _scan(script: Path, ticker: str, no_cache: bool) -> tuple[dict | None, str | None]:
    """자식의 `--json` 출력을 dict로. 실패면 (None, 사유)."""
    args = [ticker, "--json", *(["--no-cache"] if no_cache else [])]
    try:
        code, out, err = run_child(script, args)
    except OSError as exc:
        return None, f"{script.name} 실행 실패: {exc}"
    tail = (err or "").strip()[-REASON_MAX_CHARS:]
    if code != 0:
        return None, f"{script.name} exit {code}: {tail or '사유 미출력'}"
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        return None, f"{script.name} JSON 아님: {tail or out.strip()[:REASON_MAX_CHARS]}"
    if not isinstance(payload, dict):
        return None, f"{script.name} JSON 객체 아님"
    return payload, None


def _unknown(reason: str) -> dict:
    return {"status": "unknown", "reason": reason}


def build_scan_inputs(ticker: str, no_cache: bool = False) -> dict[str, dict]:
    """stage_scan·turn_scan을 돌려 `stage`·`turn`·`accumulation`을 만든다."""
    stage_json, stage_err = _scan(STAGE_SCAN, ticker, no_cache)
    turn_json, turn_err = _scan(TURN_SCAN, ticker, no_cache)

    stage_value = ((stage_json or {}).get("verdict") or {}).get("stage")
    if stage_err is None and stage_value not in {1, 2, 3, 4}:
        stage_err = f"stage_scan.py verdict.stage 없음: {stage_value!r}"
    stage = _unknown(stage_err) if stage_err else {"status": "ok", "value": stage_value}

    state = (turn_json or {}).get("state")
    if turn_err is None and state not in entry_policy.TURN_STATES:
        turn_err = f"turn_scan.py state 없음: {state!r}"
    if turn_err:
        return {"stage": stage, "turn": _unknown(turn_err), "accumulation": _unknown(turn_err)}

    # 경로 C 실적 게이트 = stage_scan `growth.latest.increased` (적자 축소도 증가). 모름이면 null.
    latest = ((stage_json or {}).get("growth") or {}).get("latest") or {}
    increased = latest.get("increased")
    accumulation = {
        "status": "ok",
        "eligible": (turn_json.get("accumulation") or {}).get("eligible") is True,
        "op_yoy_increased": increased if isinstance(increased, bool) else None,
    }
    return {"stage": stage, "turn": {"status": "ok", "value": state}, "accumulation": accumulation}


def build_valuation_inputs(valuation: Any, tranche_plan: Any) -> dict[str, Any]:
    """valuation_decision 결과에서 `valuation_grade`와 `value_gate`를 만든다."""
    try:
        result = valuation_decision.decide(valuation)
    except valuation_decision.DecisionInputError as exc:
        raise EntryDecisionError(f"valuation: {exc}") from exc
    risk = result["risk"]
    grade = result["decision"]["grade"]
    if tranche_plan is None:
        # 2단계(경로 A)는 밸류 게이트를 보지 않는다. 경로 B·C는 1차 수량이 없으면 열리지 않는다.
        return {"valuation_grade": grade, "value_gate": {
            "status": "missing", "reason": "context.tranche_plan 없음 — 경로 B·C 1차 수량 미정"}}
    if not isinstance(tranche_plan, dict) or set(tranche_plan) != set(TRANCHE_PLAN_KEYS):
        raise EntryDecisionError(f"tranche_plan must have exactly {', '.join(TRANCHE_PLAN_KEYS)}")
    gate = {
        "status": "ok",
        # 밸류 게이트 1번 「하방 막힘 — 하단목표가 ≥ 현재가」 = valuation_decision의 downside ≤ 0.
        "downside_blocked": risk["downside"] <= 0,
        "reward_risk": risk["reward_risk"],
        **tranche_plan,
    }
    return {"valuation_grade": grade, "value_gate": gate}


def check_context(context: Any) -> dict[str, Any]:
    """context JSON을 검증해 그대로 돌려준다. 모르는 키·빠진 필수 키는 `EntryDecisionError`."""
    if not isinstance(context, dict):
        raise EntryDecisionError("context must be an object")
    unknown = sorted(set(context) - set(REQUIRED_CONTEXT) - set(OPTIONAL_CONTEXT))
    if unknown:
        raise EntryDecisionError(f"context has unknown keys: {', '.join(unknown)}")
    missing = [key for key in REQUIRED_CONTEXT if key not in context]
    if missing:
        raise EntryDecisionError(f"context is missing required keys: {', '.join(missing)}")
    return context


def compose(ticker: str, valuation: Any, context: Any, asof: str | None = None,
            no_cache: bool = False) -> dict[str, Any]:
    """envelope을 조립하고 `entry_policy.decide`를 돌린다. 반환은 결과 + `inputs`."""
    context = check_context(context)
    envelope: dict[str, Any] = {
        "asof": asof or context.get("asof") or date.today().isoformat(),
        **build_valuation_inputs(valuation, context.get("tranche_plan")),
        **{key: context[key] for key in REQUIRED_CONTEXT},
        **({"leverage": context["leverage"]} if "leverage" in context else {}),
        **build_scan_inputs(ticker, no_cache),
    }
    try:
        result = entry_policy.decide(envelope)
    except entry_policy.EntryPolicyError as exc:
        raise EntryDecisionError(f"entry_policy: {exc}") from exc
    return {**result, "inputs": envelope}


def _read_json(path: str, label: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EntryDecisionError(f"{label}: invalid JSON: {exc.msg}") from exc


def main(argv: list[str] | None = None) -> int:
    """CLI — 판정을 JSON으로 stdout에 쓴다. 입력 오류도 `ok: false` JSON으로 내고 exit 2."""
    parser = argparse.ArgumentParser(description="10단계 진입 판정 — 스캔·밸류·context를 entry_policy로 결합")
    parser.add_argument("ticker", help="5단계가 확정한 6자리 티커")
    parser.add_argument("--valuation", required=True, help="valuation_decision.py 입력 JSON 파일")
    parser.add_argument("--context", required=True, help="계좌·이벤트·장대양봉·실적·종목 상태 JSON 파일")
    parser.add_argument("--asof", help="판정 기준일 (YYYY-MM-DD, 기본 context.asof 또는 오늘)")
    parser.add_argument("--no-cache", action="store_true", help="자식 스캔(stage_scan·turn_scan)에 --no-cache를 넘긴다 — 이 프로세스는 네트워크를 쓰지 않는다")
    args = parser.parse_args(argv)
    try:
        result = compose(args.ticker, _read_json(args.valuation, "valuation"),
                         _read_json(args.context, "context"), args.asof, args.no_cache)
    except (EntryDecisionError, OSError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
