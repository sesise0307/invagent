"""테스트 전체 공통 픽스처와 스킬 스크립트 로더."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from invagent.datafeed import cache, ratelimit

SKILLS_ROOT = Path(__file__).resolve().parents[1] / ".agents" / "skills"
# 테스트가 가리키기 전의 실제 캐시 위치. 기본 경로를 확인하는 테스트만 읽는다.
REAL_CACHE_ROOT = cache.CACHE_ROOT


def load_script(skill: str, name: str) -> ModuleType:
    """`.agents/skills/<skill>/scripts/<name>.py`를 새 모듈로 읽어 돌려준다.

    스킬 스크립트는 패키지가 아니라 경로로 실행되므로 테스트도 경로로 읽는다. 모듈을
    `sys.modules`에 먼저 올리는 것은 `from __future__ import annotations` 아래의 dataclass가
    자기 모듈을 찾아 타입을 해석하기 때문이다.
    """
    path = SKILLS_ROOT / skill / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _no_real_rate_limit_state(tmp_path_factory, monkeypatch):
    """속도 제한의 상태 파일과 대기는 실제 저장소·실제 시간에 닿지 않게 한다.

    가짜 `read_url`을 쓰는 테스트가 많아 상태 파일이 `output/.cache/`에 남거나 백오프가
    실제로 잠들면 스위트가 느려지고 실행 순서에 따라 결과가 달라진다.
    """
    monkeypatch.setattr(ratelimit, "STATE_ROOT", tmp_path_factory.mktemp("ratelimit"))
    monkeypatch.setattr(ratelimit, "_sleep", lambda seconds: None)


@pytest.fixture(autouse=True)
def _no_real_http_cache(tmp_path_factory, monkeypatch):
    """응답 캐시는 테스트마다 빈 임시 디렉터리를 쓴다.

    가짜 `read_url` 응답이 실제 `output/.cache/http`에 쓰이면 다음 실제 실행이 그것을 받아 쓰고,
    거꾸로 그곳에 남은 응답이 TTL 안에서 테스트의 가짜 응답을 가려 결과가 실행 시각에 따라 달라진다.
    """
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path_factory.mktemp("http-cache"))
