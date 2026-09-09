"""브리핑 준비 단계를 한 번에 돌리는 오케스트레이터.

`daily-digest` 스킬의 1-1(시장 신호) · 1-3-1(전고점 낙폭)은 서로 독립인데도 각각 별도
명령으로 순서대로 돌았다. 두 단계 모두 네트워크를 타므로 대기 시간이 그대로 더해지고,
모델이 매번 결과를 받아 다음 명령을 내느라 왕복도 그만큼 든다.

여기서는 둘을 동시에 돌리고 **입력 순서 그대로** 한 블록으로 찍는다. 각 단계는 스킬 문서가
규정한 대로 **비블로킹**이다 — 하나가 실패해도 나머지는 그대로 출력되고 종료 코드는 0이다.
브리핑 작성을 막는 것이 이 단계들의 목적이 아니기 때문이다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / ".agents" / "skills"

STEP_TIMEOUT = 180


@dataclass(frozen=True)
class Step:
    """실행할 단계 하나. `command`는 subprocess 인자 리스트, `env`는 덧씌울 환경변수다.

    캐시 끄기를 커맨드 플래그가 아니라 환경변수로 넘기는 이유: `fetch_market_signals.py`에는
    argparse가 없어 `--no-cache`를 받을 자리가 없다. 플래그로 하면 그 단계만 조용히 캐시를
    계속 쓰게 되므로, 모든 단계에 똑같이 닿는 축을 쓴다.
    """

    name: str
    command: list[str]
    env: dict[str, str] = field(default_factory=dict)


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
            env={**os.environ, **step.env} if step.env else None,
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


def build_steps(*, snapshot: Path | None, no_cache: bool) -> list[Step]:
    """오늘 돌릴 단계 목록. 입력이 없는 단계는 실패가 아니라 **미실행**으로 빠진다."""
    # `--no-cache`는 argparse가 있는 스크립트에만 붙는다. 모든 단계에 똑같이 닿도록 환경변수로 넘긴다.
    env = {"INVAGENT_HTTP_CACHE": "0"} if no_cache else {}
    steps = [
        Step(
            "시장 신호",
            [sys.executable, str(SCRIPTS / "daily-digest" / "scripts" / "fetch_market_signals.py")],
            env=env,
        )
    ]

    if snapshot and snapshot.exists():
        steps.append(
            Step(
                "전고점 낙폭",
                [
                    sys.executable,
                    str(SCRIPTS / "daily-digest" / "scripts" / "peak_drawdown.py"),
                    str(snapshot),
                    "--append",
                ],
                env=env,
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
