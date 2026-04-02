import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from invagent.core.config import Config
from invagent.core.client import TelegramClientManager
from invagent.telegram.fetch import MessageFetcher


@pytest.mark.asyncio
async def test_message_fetcher_fetch_saved_messages():
    """저장된 메시지 조회"""
    config = Config(
        api_id=123,
        api_hash="test_hash",
        session_path="/tmp/test",
        output_dir="/tmp/outputs",
    )

    manager = TelegramClientManager()
    fetcher = MessageFetcher(config, manager)

    with patch.object(manager, "get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # Mock 메시지
        mock_msg = AsyncMock()
        mock_msg.id = 1
        mock_msg.date = datetime.now(timezone.utc)
        mock_msg.text = "Test message"

        # iter_messages를 비동기 제너레이터 함수로 모킹
        async def async_gen(*args, **kwargs):
            yield mock_msg

        mock_client.iter_messages = async_gen

        messages = await fetcher.fetch_saved_messages(days=1, fetch_links=False)

        assert len(messages) > 0
        assert messages[0]["text"] == "Test message"


@pytest.mark.asyncio
async def test_message_fetcher_is_forwarded_false():
    """메시지가 포워드되지 않았을 때 is_forwarded=False"""
    config = Config(
        api_id=123,
        api_hash="test_hash",
        session_path="/tmp/test",
        output_dir="/tmp/outputs",
    )

    manager = TelegramClientManager()
    fetcher = MessageFetcher(config, manager)

    with patch.object(manager, "get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # 포워드되지 않은 메시지
        mock_msg = AsyncMock()
        mock_msg.id = 1
        mock_msg.date = datetime.now(timezone.utc)
        mock_msg.text = "직접 작성한 메시지"
        mock_msg.forward_from = None  # 포워드 아님

        async def async_gen(*args, **kwargs):
            yield mock_msg

        mock_client.iter_messages = async_gen

        messages = await fetcher.fetch_saved_messages(days=1, fetch_links=False)

        assert len(messages) > 0
        assert messages[0]["is_forwarded"] is False


@pytest.mark.asyncio
async def test_message_fetcher_is_forwarded_true():
    """메시지가 포워드되었을 때 is_forwarded=True"""
    config = Config(
        api_id=123,
        api_hash="test_hash",
        session_path="/tmp/test",
        output_dir="/tmp/outputs",
    )

    manager = TelegramClientManager()
    fetcher = MessageFetcher(config, manager)

    with patch.object(manager, "get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # 포워드된 메시지
        mock_msg = AsyncMock()
        mock_msg.id = 1
        mock_msg.date = datetime.now(timezone.utc)
        mock_msg.text = "포워드된 메시지"
        mock_msg.forward_from = "SomeUser"  # 포워드됨

        async def async_gen(*args, **kwargs):
            yield mock_msg

        mock_client.iter_messages = async_gen

        messages = await fetcher.fetch_saved_messages(days=1, fetch_links=False)

        assert len(messages) > 0
        assert messages[0]["is_forwarded"] is True


def test_message_fetcher_format_messages():
    """메시지 포맷팅"""
    config = Config(
        api_id=123,
        api_hash="test_hash",
        session_path="/tmp/test",
        output_dir="/tmp/outputs",
    )

    manager = TelegramClientManager()
    fetcher = MessageFetcher(config, manager)

    messages = [
        {
            "id": 1,
            "date": "2026-04-01 10:00",
            "text": "첫 번째 메시지",
            "links_content": ""
        },
        {
            "id": 2,
            "date": "2026-04-01 11:00",
            "text": "두 번째 메시지",
            "links_content": ""
        }
    ]

    formatted = fetcher.format_messages(messages)

    assert "2026-04-01" in formatted
    assert "첫 번째 메시지" in formatted
    assert "두 번째 메시지" in formatted
