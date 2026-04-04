import pytest
from unittest.mock import AsyncMock, patch
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
        mock_client.disconnect.assert_not_called()


@pytest.mark.asyncio
async def test_authenticate_success_path(tmp_path):
    """인증 성공 시 모든 메서드가 올바르게 호출되는지 확인"""
    config = Config(
        api_id=123,
        api_hash="test_hash",
        session_path=tmp_path / ".telegram_session",
        output_dir=tmp_path / "outputs",
    )

    with patch("invagent.core.auth.TelegramClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client

        # Mock 사용자 정보
        mock_user = AsyncMock()
        mock_user.first_name = "Test"
        mock_user.username = "testuser"
        mock_client.get_me = AsyncMock(return_value=mock_user)
        mock_client.start = AsyncMock()
        mock_client.disconnect = AsyncMock()

        result = await authenticate(config)

        # TelegramClient 초기화 검증
        mock_client_class.assert_called_once_with(
            str(config.session_path),
            config.api_id,
            config.api_hash
        )
        # 모든 메서드 호출 검증
        mock_client.start.assert_called_once()
        mock_client.get_me.assert_called_once()
        # 반환값 검증
        assert result is mock_client
        mock_client.disconnect.assert_not_called()


@pytest.mark.asyncio
async def test_authenticate_error_handling(tmp_path):
    """인증 실패 시 RuntimeError와 disconnect 처리"""
    config = Config(
        api_id=123,
        api_hash="test_hash",
        session_path=tmp_path / ".telegram_session",
        output_dir=tmp_path / "outputs",
    )

    with patch("invagent.core.auth.TelegramClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client
        mock_client.start = AsyncMock(side_effect=Exception("Connection failed"))
        mock_client.disconnect = AsyncMock()

        with pytest.raises(RuntimeError, match="인증 실패: Connection failed"):
            await authenticate(config)

        mock_client.disconnect.assert_called_once()
