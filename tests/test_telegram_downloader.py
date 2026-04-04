import pytest
from pathlib import Path
from datetime import datetime
from unittest.mock import AsyncMock, patch, MagicMock
from invagent.core.config import Config
from invagent.core.client import TelegramClientManager
from invagent.parsers.pdf_namer import PDFNamer
from invagent.telegram.downloader import PDFDownloader


@pytest.mark.asyncio
async def test_pdf_downloader_download_pdfs(tmp_path):
    """PDF 다운로드 기본 기능"""
    config = Config(
        api_id=123,
        api_hash="test_hash",
        session_path="/tmp/test",
        output_dir=tmp_path / "outputs",
    )

    manager = TelegramClientManager()
    pdf_namer = PDFNamer()
    downloader = PDFDownloader(config, manager, pdf_namer)

    with patch.object(manager, "get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # Mock entity
        mock_entity = MagicMock()
        mock_client.get_entity = AsyncMock(return_value=mock_entity)

        # Mock message with PDF
        from telethon.tl.types import MessageMediaDocument

        mock_msg = MagicMock()
        mock_msg.id = 1
        mock_msg.date = datetime.now()
        mock_msg.media = MagicMock(spec=MessageMediaDocument)
        mock_msg.media.document = MagicMock()
        mock_msg.media.document.mime_type = "application/pdf"
        mock_msg.media.document.attributes = [MagicMock(file_name="test.pdf")]

        # iter_messages를 비동기 제너레이터 함수로 모킹
        async def async_gen(*args, **kwargs):
            yield mock_msg

        mock_client.iter_messages = async_gen
        mock_client.download_media = AsyncMock()

        results = await downloader.download_pdfs(["test_channel"], days=1)

        assert "test_channel" in results
        assert len(results["test_channel"]) > 0
        assert results["test_channel"][0]["status"] == "downloaded"


def test_pdf_downloader_get_output_path(tmp_path):
    """출력 경로 생성"""
    config = Config(
        api_id=123,
        api_hash="test_hash",
        session_path="/tmp/test",
        output_dir=tmp_path / "outputs",
    )

    manager = TelegramClientManager()
    pdf_namer = PDFNamer()
    downloader = PDFDownloader(config, manager, pdf_namer)

    date = datetime(2026, 4, 2)
    path = downloader._get_output_path("test_channel", "report.pdf", date)

    assert str(path).endswith(".pdf")
    assert "2026-04-02" in str(path)


@pytest.mark.asyncio
async def test_pdf_downloader_marks_skipped_when_file_exists(tmp_path):
    """이미 같은 파일이 있으면 skipped 상태로 기록"""
    config = Config(
        api_id=123,
        api_hash="test_hash",
        session_path="/tmp/test",
        output_dir=tmp_path / "output",
    )

    manager = TelegramClientManager()
    pdf_namer = PDFNamer()
    downloader = PDFDownloader(config, manager, pdf_namer)

    with patch.object(manager, "get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_entity = MagicMock()
        mock_client.get_entity = AsyncMock(return_value=mock_entity)

        from telethon.tl.types import MessageMediaDocument

        mock_msg = MagicMock()
        mock_msg.id = 1
        mock_msg.date = datetime(2026, 4, 2)
        mock_msg.media = MagicMock(spec=MessageMediaDocument)
        mock_msg.media.document = MagicMock()
        mock_msg.media.document.mime_type = "application/pdf"
        mock_msg.media.document.attributes = [MagicMock(file_name="test.pdf")]

        output_path = downloader._get_output_path("test_channel", "기타_test.pdf", mock_msg.date)
        output_path.write_text("existing", encoding="utf-8")

        async def async_gen(*args, **kwargs):
            yield mock_msg

        mock_client.iter_messages = async_gen
        mock_client.download_media = AsyncMock()

        results = await downloader.download_pdfs(["test_channel"], days=10)

        assert results["test_channel"][0]["status"] == "skipped"
        mock_client.download_media.assert_not_called()
