import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from telethon.tl.types import (
    MessageMediaDocument,
    MessageMediaPhoto,
    MessageMediaWebPage,
)
from invagent.core.config import Config
from invagent.core.client import TelegramClientManager
from invagent.telegram.fetch import (
    MAX_IMAGE_BYTES,
    MAX_IMAGES_PER_RUN,
    PENDING_IMAGE_MARKER,
    MessageFetcher,
    _is_image_message,
)


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
        mock_msg.media = None  # 미디어 없는 순수 텍스트 메시지
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
        mock_msg.media = None  # 미디어 없는 순수 텍스트 메시지
        mock_msg.id = 1
        mock_msg.date = datetime.now(timezone.utc)
        mock_msg.text = "직접 작성한 메시지"
        mock_msg.forward = None  # 포워드 아님

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
        mock_msg.media = None  # 미디어 없는 순수 텍스트 메시지
        mock_msg.id = 1
        mock_msg.date = datetime.now(timezone.utc)
        mock_msg.text = "포워드된 메시지"
        mock_msg.forward = object()  # 포워드됨

        async def async_gen(*args, **kwargs):
            yield mock_msg

        mock_client.iter_messages = async_gen

        messages = await fetcher.fetch_saved_messages(days=1, fetch_links=False)

        assert len(messages) > 0
        assert messages[0]["is_forwarded"] is True


@pytest.mark.asyncio
async def test_message_fetcher_skips_empty_text_without_media():
    """텔레그램 링크만 남은 메시지는 제외"""
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

        mock_msg = AsyncMock()
        mock_msg.media = None  # 미디어 없는 순수 텍스트 메시지
        mock_msg.id = 1
        mock_msg.date = datetime.now(timezone.utc)
        mock_msg.text = "https://t.me/example"
        mock_msg.forward = None

        async def async_gen(*args, **kwargs):
            yield mock_msg

        mock_client.iter_messages = async_gen

        messages = await fetcher.fetch_saved_messages(days=1, fetch_links=False)

        assert messages == []


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


def test_message_fetcher_format_messages_markdown_own():
    """내 메시지 마크다운 포맷팅"""
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
            "text": "직접 작성한 메시지",
            "links_content": "",
            "is_forwarded": False,
        }
    ]

    formatted = fetcher.format_messages_markdown(messages)

    assert "**[내 메시지]**" in formatted
    assert "2026-04-01 10:00" in formatted
    assert "직접 작성한 메시지" in formatted


def test_message_fetcher_format_messages_markdown_forwarded():
    """포워드된 메시지 마크다운 포맷팅"""
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
            "text": "포워드된 메시지",
            "links_content": "",
            "is_forwarded": True,
        }
    ]

    formatted = fetcher.format_messages_markdown(messages)

    assert "**[포워드]**" in formatted
    assert "2026-04-01 10:00" in formatted
    assert "포워드된 메시지" in formatted


def test_message_fetcher_format_messages_markdown_with_links():
    """링크 내용 포함 마크다운 포맷팅"""
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
            "text": "메시지 with links",
            "links_content": "URL: https://example.com\nExample content",
            "is_forwarded": False,
        }
    ]

    formatted = fetcher.format_messages_markdown(messages)

    assert "**[내 메시지]**" in formatted
    assert "메시지 with links" in formatted
    assert "URL: https://example.com" in formatted
    assert "Example content" in formatted


# --- 이미지 첨부 처리 --------------------------------------------------------


def _image_config():
    return Config(
        api_id=123,
        api_hash="test_hash",
        session_path="/tmp/test",
        output_dir="/tmp/outputs",
    )


def _photo_message(msg_id=1, text=None, size=1024):
    """사진이 붙은 메시지 목."""
    msg = AsyncMock()
    msg.id = msg_id
    msg.date = datetime.now(timezone.utc)
    msg.text = text
    msg.forward = None
    msg.media = MagicMock(spec=MessageMediaPhoto)
    msg.file = MagicMock()
    msg.file.size = size
    return msg


def _patched_client(manager, message):
    """iter_messages가 주어진 메시지 하나만 내놓는 클라이언트 목을 만든다."""
    mock_client = AsyncMock()

    async def async_gen(*args, **kwargs):
        yield message

    mock_client.iter_messages = async_gen
    return mock_client


def test_is_image_message_accepts_photo_and_image_document():
    """사진과 image/* 문서는 이미지로 판정"""
    photo = MagicMock()
    photo.media = MagicMock(spec=MessageMediaPhoto)
    assert _is_image_message(photo) is True

    doc = MagicMock()
    doc.media = MagicMock(spec=MessageMediaDocument)
    doc.media.document = MagicMock()
    doc.media.document.mime_type = "image/png"
    assert _is_image_message(doc) is True


def test_is_image_message_rejects_webpage_preview_and_pdf():
    """링크 미리보기와 PDF는 이미지가 아니다"""
    preview = MagicMock()
    preview.media = MagicMock(spec=MessageMediaWebPage)
    assert _is_image_message(preview) is False

    pdf = MagicMock()
    pdf.media = MagicMock(spec=MessageMediaDocument)
    pdf.media.document = MagicMock()
    pdf.media.document.mime_type = "application/pdf"
    assert _is_image_message(pdf) is False

    plain = MagicMock()
    plain.media = None
    assert _is_image_message(plain) is False


@pytest.mark.asyncio
async def test_message_fetcher_keeps_caption_less_image_message(tmp_path):
    """캡션 없는 이미지 메시지도 유지하고 경로를 기록"""
    manager = TelegramClientManager()
    fetcher = MessageFetcher(_image_config(), manager)
    media_dir = tmp_path / "media" / "2026-09-03"

    with patch.object(manager, "get_client") as mock_get_client:
        mock_client = _patched_client(manager, _photo_message(text=None))
        saved_path = str(media_dir / "1.jpg")
        mock_client.download_media = AsyncMock(return_value=saved_path)
        mock_get_client.return_value = mock_client

        messages = await fetcher.fetch_saved_messages(days=1, media_dir=media_dir)

    assert len(messages) == 1
    assert messages[0]["text"] == ""
    assert messages[0]["images"] == [saved_path]


@pytest.mark.asyncio
async def test_message_fetcher_keeps_image_when_caption_is_only_a_telegram_link(tmp_path):
    """캡션이 t.me 링크뿐이라 비어도 이미지가 있으면 살린다"""
    manager = TelegramClientManager()
    fetcher = MessageFetcher(_image_config(), manager)
    media_dir = tmp_path / "media"

    with patch.object(manager, "get_client") as mock_get_client:
        mock_client = _patched_client(
            manager, _photo_message(text="https://t.me/example")
        )
        mock_client.download_media = AsyncMock(return_value=str(media_dir / "1.jpg"))
        mock_get_client.return_value = mock_client

        messages = await fetcher.fetch_saved_messages(days=1, media_dir=media_dir)

    assert len(messages) == 1
    assert messages[0]["images"] != []


@pytest.mark.asyncio
async def test_message_fetcher_survives_download_failure(tmp_path):
    """다운로드가 실패해도 메시지는 버리지 않고 센티널을 남긴다"""
    manager = TelegramClientManager()
    fetcher = MessageFetcher(_image_config(), manager)

    with patch.object(manager, "get_client") as mock_get_client:
        mock_client = _patched_client(manager, _photo_message(text="차트"))
        mock_client.download_media = AsyncMock(side_effect=OSError("disk full"))
        mock_get_client.return_value = mock_client

        messages = await fetcher.fetch_saved_messages(days=1, media_dir=tmp_path)

    assert len(messages) == 1
    assert messages[0]["text"] == "차트"
    assert messages[0]["images"][0].startswith("[이미지 저장 실패:")
    assert "disk full" in messages[0]["images"][0]


@pytest.mark.asyncio
async def test_message_fetcher_skips_oversized_image(tmp_path):
    """용량 상한을 넘으면 다운로드를 시도하지 않는다"""
    manager = TelegramClientManager()
    fetcher = MessageFetcher(_image_config(), manager)

    with patch.object(manager, "get_client") as mock_get_client:
        oversized = _photo_message(text="큰 이미지", size=MAX_IMAGE_BYTES + 1)
        mock_client = _patched_client(manager, oversized)
        mock_client.download_media = AsyncMock()
        mock_get_client.return_value = mock_client

        messages = await fetcher.fetch_saved_messages(days=1, media_dir=tmp_path)

    mock_client.download_media.assert_not_called()
    assert messages[0]["images"][0].startswith("[이미지 건너뜀: 용량 초과")


@pytest.mark.asyncio
async def test_message_fetcher_enforces_per_run_image_budget(tmp_path):
    """실행당 상한을 넘긴 이미지는 건너뛴다"""
    manager = TelegramClientManager()
    fetcher = MessageFetcher(_image_config(), manager)
    total = MAX_IMAGES_PER_RUN + 2

    with patch.object(manager, "get_client") as mock_get_client:
        mock_client = AsyncMock()

        async def async_gen(*args, **kwargs):
            for msg_id in range(1, total + 1):
                yield _photo_message(msg_id=msg_id, text=f"이미지 {msg_id}")

        mock_client.iter_messages = async_gen
        mock_client.download_media = AsyncMock(
            side_effect=lambda media, file: f"{file}.jpg"
        )
        mock_get_client.return_value = mock_client

        messages = await fetcher.fetch_saved_messages(days=1, media_dir=tmp_path)

    assert len(messages) == total
    assert mock_client.download_media.await_count == MAX_IMAGES_PER_RUN
    skipped = [m for m in messages if m["images"][0].startswith("[이미지 건너뜀: 실행당 상한")]
    assert len(skipped) == 2


@pytest.mark.asyncio
async def test_message_fetcher_skips_download_without_media_dir(tmp_path):
    """media_dir이 없으면 다운로드하지 않는다"""
    manager = TelegramClientManager()
    fetcher = MessageFetcher(_image_config(), manager)

    with patch.object(manager, "get_client") as mock_get_client:
        mock_client = _patched_client(manager, _photo_message(text="캡션"))
        mock_client.download_media = AsyncMock()
        mock_get_client.return_value = mock_client

        messages = await fetcher.fetch_saved_messages(days=1, media_dir=None)

    mock_client.download_media.assert_not_called()
    assert messages[0]["images"] == []


def test_format_messages_markdown_renders_pending_image_marker():
    """이미지 블록은 파일 경로와 판독 대기 마커를 낸다"""
    fetcher = MessageFetcher(_image_config(), TelegramClientManager())

    formatted = fetcher.format_messages_markdown(
        [
            {
                "id": 1,
                "date": "2026-09-03 10:00",
                "text": "차트 첨부",
                "links_content": "",
                "is_forwarded": True,
                "images": ["output/telegram-daily/media/2026-09-03/1.jpg"],
            }
        ]
    )

    assert "**[포워드]**" in formatted
    assert "이미지:" in formatted
    assert "> 파일: output/telegram-daily/media/2026-09-03/1.jpg" in formatted
    assert f"> {PENDING_IMAGE_MARKER}" in formatted


def test_format_messages_markdown_image_only_message_has_no_blank_body():
    """이미지 전용 메시지는 헤더 다음에 빈 본문 줄을 만들지 않는다"""
    fetcher = MessageFetcher(_image_config(), TelegramClientManager())

    formatted = fetcher.format_messages_markdown(
        [
            {
                "id": 1,
                "date": "2026-09-03 10:00",
                "text": "",
                "links_content": "",
                "is_forwarded": True,
                "images": ["media/1.jpg"],
            }
        ]
    )

    lines = formatted.split("\n")
    assert lines[0] == "**[포워드]** 2026-09-03 10:00"
    assert lines[1] == ""
    assert lines[2] == "이미지:"


def test_format_messages_markdown_failed_image_has_no_marker():
    """저장 실패 항목은 판독할 파일이 없으므로 마커를 붙이지 않는다"""
    fetcher = MessageFetcher(_image_config(), TelegramClientManager())

    formatted = fetcher.format_messages_markdown(
        [
            {
                "id": 1,
                "date": "2026-09-03 10:00",
                "text": "본문",
                "links_content": "",
                "is_forwarded": False,
                "images": ["[이미지 저장 실패: disk full]"],
            }
        ]
    )

    assert "> [이미지 저장 실패: disk full]" in formatted
    assert PENDING_IMAGE_MARKER not in formatted
