"""저장소 경로와 환경 설정 값 해석.

스킬 스크립트마다 `Path(__file__).parents[N]`으로 저장소 루트를 세던 것을 한 곳으로
모은다. 깊이 상수는 파일이 옮겨질 때마다 조용히 틀리므로, 마커 파일을 찾아 올라간다.
"""

from __future__ import annotations

import os
from pathlib import Path

# 저장소 루트임을 알리는 마커. 설치본이 아니라 작업 저장소를 찾는 것이 목적이다.
ROOT_MARKERS = ("pyproject.toml", ".git")

ENV_OUTPUT_DIR = "INVAGENT_OUTPUT_DIR"


def repo_root() -> Path:
    """마커 파일이 있는 가장 가까운 상위 디렉터리. 못 찾으면 현재 작업 디렉터리."""
    for parent in Path(__file__).resolve().parents:
        if any((parent / marker).exists() for marker in ROOT_MARKERS):
            return parent
    return Path.cwd()


def output_dir() -> Path:
    """산출물 디렉터리. 환경변수 우선."""
    override = os.environ.get(ENV_OUTPUT_DIR)
    return Path(override).expanduser() if override else repo_root() / "output"


def env_value(name: str, start: Path | None = None) -> str | None:
    """설정 값을 환경변수 → 가장 가까운 `.env` 순으로 찾는다.

    저장소는 dotenv 라이브러리를 쓰지 않으므로 `.env`를 직접 훑는다. 값 자체는
    자격증명일 수 있으니 어디에도 출력하지 않는다.
    """
    value = os.environ.get(name, "").strip()
    if value:
        return value

    origin = (start or Path(__file__)).resolve()
    candidates = [origin, *origin.parents] if origin.is_dir() else list(origin.parents)
    for parent in candidates:
        env_path = parent / ".env"
        if not env_path.is_file():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, raw = line.partition("=")
            if key.strip() == name:
                return raw.strip().strip("'\"") or None
        # 가장 가까운 `.env` 하나만 본다. 더 위의 파일이 값을 덮어쓰지 않는다.
        break
    return None
