import asyncio

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
from invagent.telegram import fetch as fetch_module
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


async def _never_finishes(media, file):
    """미디어 DC 연결이 멈춘 다운로드를 흉내 낸다."""
    await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_message_fetcher_gives_up_on_stalled_image_download(tmp_path, monkeypatch):
    """끝나지 않는 다운로드는 시간 초과 센티널을 남기고 실행을 끝낸다"""
    monkeypatch.setattr(fetch_module, "IMAGE_DOWNLOAD_TIMEOUT_SECONDS", 0.05)
    manager = TelegramClientManager()
    fetcher = MessageFetcher(_image_config(), manager)

    with patch.object(manager, "get_client") as mock_get_client:
        mock_client = _patched_client(manager, _photo_message(text="차트"))
        mock_client.download_media = AsyncMock(side_effect=_never_finishes)
        mock_get_client.return_value = mock_client

        messages = await asyncio.wait_for(
            fetcher.fetch_saved_messages(days=1, media_dir=tmp_path), timeout=2
        )

    assert len(messages) == 1
    assert messages[0]["text"] == "차트"
    assert messages[0]["images"][0].startswith("[이미지 저장 실패: 시간 초과")


@pytest.mark.asyncio
async def test_message_fetcher_retries_stalled_image_download_once(tmp_path, monkeypatch):
    """첫 시도가 멈춰도 한 번 더 시도해 받으면 경로를 남긴다"""
    monkeypatch.setattr(fetch_module, "IMAGE_DOWNLOAD_TIMEOUT_SECONDS", 0.05)
    manager = TelegramClientManager()
    fetcher = MessageFetcher(_image_config(), manager)
    saved_path = str(tmp_path / "1.jpg")
    attempts = []

    async def stall_then_succeed(media, file):
        attempts.append(file)
        if len(attempts) == 1:
            await asyncio.Event().wait()
        return saved_path

    with patch.object(manager, "get_client") as mock_get_client:
        mock_client = _patched_client(manager, _photo_message(text="차트"))
        mock_client.download_media = AsyncMock(side_effect=stall_then_succeed)
        mock_get_client.return_value = mock_client

        messages = await asyncio.wait_for(
            fetcher.fetch_saved_messages(days=1, media_dir=tmp_path), timeout=2
        )

    assert messages[0]["images"] == [saved_path]
    assert mock_client.download_media.await_count == 2


@pytest.mark.asyncio
async def test_message_fetcher_skips_remaining_images_after_timeout(tmp_path, monkeypatch):
    """한 이미지가 끝내 시간 초과되면 이번 실행의 나머지 이미지는 받지 않는다"""
    monkeypatch.setattr(fetch_module, "IMAGE_DOWNLOAD_TIMEOUT_SECONDS", 0.05)
    manager = TelegramClientManager()
    fetcher = MessageFetcher(_image_config(), manager)

    with patch.object(manager, "get_client") as mock_get_client:
        mock_client = AsyncMock()

        async def async_gen(*args, **kwargs):
            yield _photo_message(msg_id=1, text="첫 차트")
            yield _photo_message(msg_id=2, text="둘째 차트")

        mock_client.iter_messages = async_gen
        mock_client.download_media = AsyncMock(side_effect=_never_finishes)
        mock_get_client.return_value = mock_client

        messages = await asyncio.wait_for(
            fetcher.fetch_saved_messages(days=1, media_dir=tmp_path), timeout=2
        )

    assert len(messages) == 2
    assert messages[0]["images"][0].startswith("[이미지 저장 실패: 시간 초과")
    assert messages[1]["images"] == ["[이미지 건너뜀: 앞선 다운로드 시간 초과]"]
    assert mock_client.download_media.await_count == 2


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
                "images": ["output/daily-digest/media/2026-09-03/1.jpg"],
            }
        ]
    )

    assert "**[포워드]**" in formatted
    assert "이미지:" in formatted
    assert "> 파일: output/daily-digest/media/2026-09-03/1.jpg" in formatted
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

@pytest.mark.asyncio
async def test_message_fetcher_extracts_links_concurrently(monkeypatch):
    """링크 추출이 메시지마다 직렬이면 하드 타임아웃이 메시지 수만큼 쌓인다.

    1,500건을 따라잡는 날 이 루프가 실행 시간을 통째로 지배한다. 동시에 돌리되
    출력 순서는 메시지 순서 그대로여야 한다 — 브리핑이 시간순으로 읽히기 때문이다.
    """
    import asyncio
    import time

    config = Config(api_id=123, api_hash="h", session_path="/tmp/s", output_dir="/tmp/o")
    fetcher = MessageFetcher(config, TelegramClientManager())

    live = peak_live = 0

    async def slow_extract(text):
        nonlocal live, peak_live
        live += 1
        peak_live = max(peak_live, live)
        await asyncio.sleep(0.05)
        live -= 1
        return {"contents": {f"https://e.test/{text}": f"body-{text}"}}

    monkeypatch.setattr(fetcher.link_extractor, "extract_and_fetch", slow_extract)
    monkeypatch.setattr(fetcher.link_extractor, "remove_telegram_urls", lambda t: t)

    msgs = []
    for i in range(8):
        m = AsyncMock()
        m.media = None
        m.id = i
        m.date = datetime.now(timezone.utc)
        m.text = f"m{i}"
        m.forward = None
        msgs.append(m)

    with patch.object(fetcher.client_manager, "get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        async def async_gen(*args, **kwargs):
            for m in msgs:
                yield m

        mock_client.iter_messages = async_gen

        started = time.monotonic()
        messages = await fetcher.fetch_saved_messages(days=1, fetch_links=True)
        elapsed = time.monotonic() - started

    assert [m["text"] for m in messages] == [f"m{i}" for i in range(8)], "출력 순서가 흐트러졌다"
    assert messages[3]["links_content"].endswith("body-m3")
    assert peak_live > 1, "링크를 아직 한 건씩 받고 있다"
    assert elapsed < 8 * 0.05, "직렬 실행 시간이 그대로 나온다"


# --- 링크 원문 분리 --------------------------------------------------


def _link_message(text, msg_id=1):
    """링크만 담긴 텍스트 메시지 목."""
    msg = AsyncMock()
    msg.media = None
    msg.id = msg_id
    msg.date = datetime.now(timezone.utc)
    msg.text = text
    msg.forward = None
    return msg


@pytest.mark.asyncio
async def test_fetch_saved_messages_moves_naver_blog_body_to_a_file(tmp_path, monkeypatch):
    """네이버 블로그 글 원문은 파일로 빼고 링크 블록엔 경로와 요약 대기 마커만 남긴다"""
    fetcher = MessageFetcher(_image_config(), TelegramClientManager())
    url = "https://blog.naver.com/chacha36/224407253026"
    body = "HD현대중공업 발전엔진 증설 팔로업입니다.\n본문 둘째 줄"

    async def fake_extract(text):
        return {"contents": {url: body}}

    monkeypatch.setattr(fetcher.link_extractor, "extract_and_fetch", fake_extract)
    link_dir = tmp_path / "links" / "2026-09-10"

    with patch.object(fetcher.client_manager, "get_client") as mock_get_client:
        mock_get_client.return_value = _patched_client(fetcher.client_manager, _link_message(url))
        messages = await fetcher.fetch_saved_messages(days=1, fetch_links=True, link_dir=link_dir)

    saved = link_dir / "chacha36_224407253026.md"
    assert saved.read_text(encoding="utf-8") == f"URL: {url}\n\n{body}\n"
    assert messages[0]["links_content"] == f"URL: {url}\n파일: {saved}\n[요약 대기]"


@pytest.mark.asyncio
async def test_fetch_saved_messages_moves_every_link_body_to_a_file(tmp_path, monkeypatch):
    """네이버 글이 아닌 링크 본문도 파일로 빼고 링크 블록엔 경로와 요약 대기 마커만 남긴다"""
    fetcher = MessageFetcher(_image_config(), TelegramClientManager())
    naver = "https://blog.naver.com/chacha36/224407253026"
    other = "https://example.com/report"

    async def fake_extract(text):
        return {"contents": {naver: "블로그 원문", other: "일반 링크 본문"}}

    monkeypatch.setattr(fetcher.link_extractor, "extract_and_fetch", fake_extract)
    link_dir = tmp_path / "links"

    with patch.object(fetcher.client_manager, "get_client") as mock_get_client:
        mock_get_client.return_value = _patched_client(
            fetcher.client_manager, _link_message(f"{naver} {other}")
        )
        messages = await fetcher.fetch_saved_messages(days=1, fetch_links=True, link_dir=link_dir)

    other_file = link_dir / "example.com_43ecaebf74.md"
    assert other_file.read_text(encoding="utf-8") == f"URL: {other}\n\n일반 링크 본문\n"
    assert f"URL: {other}\n파일: {other_file}\n[요약 대기]" in messages[0]["links_content"]
    assert "일반 링크 본문" not in messages[0]["links_content"]
    assert sorted(p.name for p in link_dir.iterdir()) == [
        "chacha36_224407253026.md",
        "example.com_43ecaebf74.md",
    ]


@pytest.mark.asyncio
async def test_fetch_saved_messages_leaves_failed_naver_fetch_inline(tmp_path, monkeypatch):
    """네이버 글이라도 읽기에 실패했으면 파일도 마커도 없이 실패 표시만 남긴다"""
    fetcher = MessageFetcher(_image_config(), TelegramClientManager())
    url = "https://blog.naver.com/chacha36/224407253026"

    async def fake_extract(text):
        from invagent.telegram.link_extractor import LinkFailure

        return {"contents": {url: LinkFailure("[링크 읽기 타임아웃]")}}

    monkeypatch.setattr(fetcher.link_extractor, "extract_and_fetch", fake_extract)
    link_dir = tmp_path / "links"

    with patch.object(fetcher.client_manager, "get_client") as mock_get_client:
        mock_get_client.return_value = _patched_client(fetcher.client_manager, _link_message(url))
        messages = await fetcher.fetch_saved_messages(days=1, fetch_links=True, link_dir=link_dir)

    assert messages[0]["links_content"] == f"URL: {url}\n[링크 읽기 타임아웃]"
    assert not link_dir.exists()


@pytest.mark.asyncio
async def test_fetch_saved_messages_saves_pdf_link_as_pdf_file(tmp_path, monkeypatch):
    """PDF 링크는 원본 바이트 그대로 `.pdf` 파일로 저장하고 요약 대기 마커를 남긴다"""
    from invagent.telegram.link_extractor import PdfDocument

    fetcher = MessageFetcher(_image_config(), TelegramClientManager())
    url = "https://vo.la/zapQlKL"
    pdf = b"%PDF-1.7\r\n%\xe2\xe3\xcf\xd3\r\n"

    async def fake_extract(text):
        return {"contents": {url: PdfDocument(pdf)}}

    monkeypatch.setattr(fetcher.link_extractor, "extract_and_fetch", fake_extract)
    link_dir = tmp_path / "links"

    with patch.object(fetcher.client_manager, "get_client") as mock_get_client:
        mock_get_client.return_value = _patched_client(fetcher.client_manager, _link_message(url))
        messages = await fetcher.fetch_saved_messages(days=1, fetch_links=True, link_dir=link_dir)

    [saved] = list(link_dir.iterdir())
    assert saved.name.startswith("vo.la_") and saved.suffix == ".pdf"
    assert saved.read_bytes() == pdf
    assert messages[0]["links_content"] == f"URL: {url}\n파일: {saved}\n[요약 대기]"


@pytest.mark.asyncio
async def test_fetch_saved_messages_saves_body_that_starts_with_a_bracket(tmp_path, monkeypatch):
    """「[일진전기] 단일판매…」처럼 대괄호로 시작하는 정상 본문도 실패로 보지 않고 파일로 뺀다"""
    fetcher = MessageFetcher(_image_config(), TelegramClientManager())
    url = "https://www.awakeplus.co.kr/data/view/20261002800002"
    body = "[일진전기] 단일판매ㆍ공급계약체결\n계약금액 : 1,872억"

    async def fake_extract(text):
        return {"contents": {url: body}}

    monkeypatch.setattr(fetcher.link_extractor, "extract_and_fetch", fake_extract)
    link_dir = tmp_path / "links"

    with patch.object(fetcher.client_manager, "get_client") as mock_get_client:
        mock_get_client.return_value = _patched_client(fetcher.client_manager, _link_message(url))
        messages = await fetcher.fetch_saved_messages(days=1, fetch_links=True, link_dir=link_dir)

    [saved] = list(link_dir.iterdir())
    assert saved.read_text(encoding="utf-8") == f"URL: {url}\n\n{body}\n"
    assert messages[0]["links_content"] == f"URL: {url}\n파일: {saved}\n[요약 대기]"
