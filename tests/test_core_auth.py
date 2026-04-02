import pytest
from unittest.mock import AsyncMock, patch
from pathlib import Path
from invagent.core.config import Config
from invagent.core.auth import authenticate


@pytest.mark.asyncio
async def test_authenticate_creates_session_file(tmp_path):
    """authenticate 호출 후 세션 파일 생성 여부 확인"""
    config = Config(
        api_id=123,
        api_hash="test_hash",
        session_path=tmp_path / ".telegram_session",
        output_dir=tmp_path / "outputs",
    )

    with patch("invagent.core.auth.TelegramClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client
        mock_client.get_me = AsyncMock(return_value=AsyncMock(first_name="Test", username="testuser"))

        client = await authenticate(config)

        # TelegramClient가 올바른 파라미터로 초기화됨
        mock_client_class.assert_called_once_with(
            str(config.session_path),
            config.api_id,
            config.api_hash
        )
        mock_client.start.assert_called_once()
