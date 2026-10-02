"""테스트 전체 공통 픽스처."""

import pytest

from invagent.datafeed import ratelimit


@pytest.fixture(autouse=True)
def _no_real_rate_limit_state(tmp_path_factory, monkeypatch):
    """속도 제한의 상태 파일과 대기는 실제 저장소·실제 시간에 닿지 않게 한다.

    가짜 `read_url`을 쓰는 테스트가 많아 상태 파일이 `output/.cache/`에 남거나 백오프가
    실제로 잠들면 스위트가 느려지고 실행 순서에 따라 결과가 달라진다.
    """
    monkeypatch.setattr(ratelimit, "STATE_ROOT", tmp_path_factory.mktemp("ratelimit"))
    monkeypatch.setattr(ratelimit, "_sleep", lambda seconds: None)
