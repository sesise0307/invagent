"""브리핑 준비 단계를 한 번에 돌리는 오케스트레이터.

`daily-digest` 스킬의 1-1(시장 신호) · 1-3-1(전고점 낙폭) · 1-5단계 5)(DART 공시 대조)는 서로 독립인데도 각각 별도
명령으로 순서대로 돌았다. 모든 단계가 네트워크를 타므로 대기 시간이 그대로 더해지고,
모델이 매번 결과를 받아 다음 명령을 내느라 왕복도 그만큼 든다.

여기서는 이들을 동시에 돌리고 **입력 순서 그대로** 한 블록으로 찍는다. 각 단계는 스킬 문서가
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

# 단계 하나가 넘기면 죽이는 상한(초). 길이를 정하는 것은 StockEasy다 — 8초 간격·동시 1건이 프로세스를
# 넘어 공유되므로 시장 신호(무인증 4건)와 전고점 낙폭(보유 종목당 `info-tab` 1건, 금융위 일봉이 빈
# 평일 오전·15:40 뒤)이 한 줄로 선다. 보유 상한 12종목(매매규칙 9) + 4건 = 16건 × 8초 = 128초에,
# 한 요청이 재시도를 다 쓰는 경우(3회 × HTTP 20초 + 대기 최대 30초 × 2 ≈ 120초, 그 뒤엔 cool-down이라
# 나머지는 즉시 실패)를 더해 약 250초 — 여유를 두고 300초. 2026-10-28 실측: 8종목·캐시 미스면 StockEasy
# 요청 간격 중앙값 8.0초로 12건이 약 90초. 180초는 정상 경로는 덮지만 재시도 한 번이면 넘었다.
STEP_TIMEOUT = 300


@dataclass(frozen=True)
class Step:
    """실행할 단계 하나. `command`는 subprocess 인자 리스트, `env`는 덧씌울 환경변수다.

    캐시 끄기를 커맨드 플래그가 아니라 환경변수로 넘기는 이유: 단계마다 플래그를 따로 붙이면
    새 단계 하나가 빠졌을 때 그 단계만 조용히 캐시를 계속 쓴다. 환경변수는 스크립트가 어떤
    플래그를 받든 모든 단계에 똑같이 닿는다.
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
    timed_out: bool = False


def _text(output: bytes | str | None) -> str:
    """`TimeoutExpired`의 출력은 `text=True`여도 POSIX에서는 bytes로 온다."""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output or ""


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
    except subprocess.TimeoutExpired as e:
        # 죽기 전까지 받은 출력은 남긴다. 예외 문자열은 명령 경로가 앞을 다 먹어 사유가 안 보인다.
        return StepResult(step.name, False, _text(e.stdout), _text(e.stderr), timed_out=True)
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

    dart = [sys.executable, str(SCRIPTS / "daily-digest" / "scripts" / "dart_scan.py")]
    if snapshot and snapshot.exists():
        dart += ["--snapshot", str(snapshot)]
    # 오늘 스냅샷이 아직 없어도 돈다 — 스크립트가 최신 스냅샷의 보유 종목을 쓴다.
    steps.append(Step("DART 공시", dart, env=env))

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
        if r.timed_out:
            label = "부분 출력" if r.stdout.strip() else f"{STEP_TIMEOUT:.0f}초 안에 출력 없음"
            out.append(f"(시간 초과 — {label})")
            if r.stdout.strip():
                out.append(r.stdout.rstrip())
            if r.stderr.strip():
                out.append(f"[누락] {r.stderr.strip()[:300]}")
        elif r.ok:
            out.append(r.stdout.rstrip() or "(출력 없음)")
            if r.stderr.strip():
                out.append(f"[누락] {r.stderr.strip()}")
        else:
            out.append(f"(수집 실패 — {r.stderr.strip()[:300] or '사유 없음'})")
        out.append("")
    return "\n".join(out)
