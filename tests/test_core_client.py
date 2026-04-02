import pytest
from unittest.mock import AsyncMock, patch
from invagent.core.config import Config
from invagent.core.client import TelegramClientManager


@pytest.mark.asyncio
async def test_client_manager_returns_same_instance():
    """get_client() 호출 시 동일한 인스턴스 반환"""
    config = Config(
        api_id=123,
        api_hash="test_hash",
        session_path="/tmp/test_session",
        output_dir="/tmp/outputs",
    )

    manager = TelegramClientManager()

    with patch("invagent.core.client.TelegramClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client

        client1 = await manager.get_client(config)
        client2 = await manager.get_client(config)

        assert client1 is client2, "같은 manager에서는 동일한 클라이언트 반환"


@pytest.mark.asyncio
async def test_client_manager_connects_only_once():
    """connect()는 1회만 호출"""
    config = Config(
        api_id=123,
        api_hash="test_hash",
        session_path="/tmp/test_session",
        output_dir="/tmp/outputs",
    )

    manager = TelegramClientManager()

    with patch("invagent.core.client.TelegramClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client

        await manager.get_client(config)
        await manager.get_client(config)

        # connect()는 1회만 호출
        assert mock_client.connect.call_count == 1
