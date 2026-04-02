import os
from pathlib import Path
from invagent.core.config import Config


def test_config_from_env_with_valid_env_vars(monkeypatch):
    """환경변수에서 설정을 정상적으로 로드"""
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123def456")

    config = Config.from_env()

    assert config.api_id == 12345
    assert config.api_hash == "abc123def456"
    assert config.session_path == Path.home() / ".telegram_session"
    assert config.output_dir == Path("outputs")


def test_config_from_env_missing_api_id(monkeypatch):
    """TELEGRAM_API_ID 누락 시 ValueError 발생"""
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123")

    try:
        Config.from_env()
        assert False, "ValueError should be raised"
    except ValueError as e:
        assert "TELEGRAM_API_ID" in str(e)


def test_config_from_env_missing_api_hash(monkeypatch):
    """TELEGRAM_API_HASH 누락 시 ValueError 발생"""
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)

    try:
        Config.from_env()
        assert False, "ValueError should be raised"
    except ValueError as e:
        assert "TELEGRAM_API_HASH" in str(e)
