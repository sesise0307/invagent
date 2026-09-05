"""브리핑 준비 단계를 한 번에 돌리는 오케스트레이터.

`summarize-telegram` 스킬의 1-1(시장 신호) · 1-1-1(현금 투입 사다리) · 1-3-1(전고점 낙폭)은
서로 독립인데도 각각 별도 명령으로 순서대로 돌았다. 세 단계는 모두 네트워크를 타므로 대기
시간이 그대로 더해지고, 모델이 매번 결과를 받아 다음 명령을 내느라 왕복도 세 번 든다.

여기서는 셋을 동시에 돌리고 **입력 순서 그대로** 한 블록으로 찍는다. 각 단계는 스킬 문서가
규정한 대로 **비블로킹**이다 — 하나가 실패해도 나머지는 그대로 출력되고 종료 코드는 0이다.
브리핑 작성을 막는 것이 이 단계들의 목적이 아니기 때문이다.
"""

from __future__ import annotations

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / ".agents" / "skills"

STEP_TIMEOUT = 180


@dataclass(frozen=True)
class Step:
    """실행할 단계 하나. `command`는 subprocess 인자 리스트다."""

    name: str
    command: list[str]


@dataclass(frozen=True)
class StepResult:
    name: str
    ok: bool
    stdout: str
    stderr: str


def _run(step: Step) -> StepResult:
    try:
        proc = subprocess.run(
            step.command,
            capture_output=True,
            text=True,
            timeout=STEP_TIMEOUT,
            cwd=REPO_ROOT,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return StepResult(step.name, False, "", str(e)[:200])
    return StepResult(step.name, proc.returncode == 0, proc.stdout, proc.stderr)


def run_steps(steps: list[Step]) -> list[StepResult]:
    """단계들을 동시에 돌리고 **입력 순서대로** 결과를 돌려준다."""
    if not steps:
        return []
    with ThreadPoolExecutor(max_workers=len(steps)) as pool:
        return list(pool.map(_run, steps))


def build_steps(
    *,
    snapshot: Path | None,
    vkospi: float | None,
    net_buy_days: int | None,
    no_cache: bool,
) -> list[Step]:
    """오늘 돌릴 단계 목록. 입력이 없는 단계는 실패가 아니라 **미실행**으로 빠진다."""
    cache_flag = ["--no-cache"] if no_cache else []
    steps = [
        Step(
            "시장 신호",
            [sys.executable, str(SCRIPTS / "summarize-telegram" / "scripts" / "fetch_market_signals.py")],
        )
    ]

    ladder = [sys.executable, str(SCRIPTS / "summarize-telegram" / "scripts" / "cash_deploy_check.py")]
    # VKOSPI·수급 일수는 무인증 수집 경로가 없어 인자로만 들어온다. 안 주면 붙이지 않는다 —
    # 해당 문항은 ❓로 남고, ❓는 절대 충족으로 승격되지 않는다.
    if vkospi is not None:
        ladder += ["--vkospi", str(vkospi)]
    if net_buy_days is not None:
        ladder += ["--net-buy-days", str(net_buy_days)]
    steps.append(Step("현금 투입 사다리", ladder + cache_flag))

    if snapshot and snapshot.exists():
        steps.append(
            Step(
                "전고점 낙폭",
                [
                    sys.executable,
                    str(SCRIPTS / "summarize-telegram" / "scripts" / "peak_drawdown.py"),
                    str(snapshot),
                    "--append",
                ]
                + cache_flag,
            )
        )
    return steps


def render(results: list[StepResult]) -> str:
    """단계 결과를 한 블록으로. 실패한 단계는 사유만 남기고 자리를 지킨다."""
    out: list[str] = []
    for r in results:
        out.append(f"### {r.name}")
        if r.ok:
            out.append(r.stdout.rstrip() or "(출력 없음)")
            if r.stderr.strip():
                out.append(f"[누락] {r.stderr.strip()}")
        else:
            out.append(f"(수집 실패 — {r.stderr.strip()[:300] or '사유 없음'})")
        out.append("")
    return "\n".join(out)
