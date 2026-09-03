import os
from pathlib import Path
import pytest
from invagent.core.config import Config


def test_config_from_env_with_valid_env_vars(monkeypatch):
    """환경변수에서 설정을 정상적으로 로드"""
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123def456")

    config = Config.from_env()

    assert config.api_id == 12345
    assert config.api_hash == "abc123def456"
    assert config.session_path == Path.home() / ".telegram_session"
    assert config.output_dir == Path("output")


def test_config_from_env_with_optional_path_overrides(monkeypatch):
    """선택 환경변수로 세션/출력 경로를 덮어쓸 수 있다."""
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123def456")
    monkeypatch.setenv("TELEGRAM_SESSION_PATH", "~/custom.session")
    monkeypatch.setenv("INVAGENT_OUTPUT_DIR", "~/invagent-output")

    config = Config.from_env()

    assert config.session_path == Path("~/custom.session").expanduser()
    assert config.output_dir == Path("~/invagent-output").expanduser()


def test_config_from_env_missing_api_id(monkeypatch):
    """TELEGRAM_API_ID 누락 시 ValueError 발생"""
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123")

    with pytest.raises(ValueError, match="TELEGRAM_API_ID"):
        Config.from_env()


def test_config_from_env_missing_api_hash(monkeypatch):
    """TELEGRAM_API_HASH 누락 시 ValueError 발생"""
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)

    with pytest.raises(ValueError, match="TELEGRAM_API_HASH"):
        Config.from_env()


def test_config_from_env_invalid_api_id(monkeypatch):
    """TELEGRAM_API_ID가 정수가 아니면 ValueError 발생"""
    monkeypatch.setenv("TELEGRAM_API_ID", "not_an_integer")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123")

    with pytest.raises(ValueError, match="TELEGRAM_API_ID는 정수여야"):
        Config.from_env()


def test_config_telegram_media_dir_is_sibling_of_raw_dir():
    """미디어 디렉토리는 raw 디렉토리의 형제로 날짜별 하위 폴더를 쓴다"""
    config = Config(
        api_id=123,
        api_hash="abc123",
        session_path=Path("/tmp/session"),
        output_dir=Path("/tmp/outputs"),
    )

    assert config.telegram_daily_dir() == Path("/tmp/outputs/telegram-daily/raw")
    assert config.telegram_media_dir("2026-09-03") == Path(
        "/tmp/outputs/telegram-daily/media/2026-09-03"
    )
