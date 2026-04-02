"""
Telegram 채널에서 PDF를 다운로드하는 PDFDownloader 클래스.

이 모듈은 Telegram 채널에서 PDF 파일을 찾아 다운로드하고,
일관된 파일명으로 저장하는 기능을 제공합니다.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from telethon.tl.types import MessageMediaDocument

from invagent.core.config import Config
from invagent.core.client import TelegramClientManager
from invagent.parsers.pdf_namer import PDFNamer


class PDFDownloader:
    """Telegram 채널에서 PDF를 다운로드하는 클래스."""

    def __init__(
        self,
        config: Config,
        client_manager: TelegramClientManager,
        pdf_namer: PDFNamer,
    ) -> None:
        """
        PDFDownloader를 초기화합니다.

        Args:
            config: Config 인스턴스
            client_manager: TelegramClientManager 인스턴스
            pdf_namer: PDFNamer 인스턴스
        """
        self.config = config
        self.client_manager = client_manager
        self.pdf_namer = pdf_namer

    async def download_pdfs(
        self, channels: list[str], days: int = 1
    ) -> dict[str, list[dict]]:
        """
        각 채널에서 PDF를 찾아 다운로드합니다.

        cutoff date 이전 메시지는 처리하지 않습니다.

        Args:
            channels: 다운로드할 채널 목록
            days: 조회할 기간 (일 단위). 기본값: 1

        Returns:
            다운로드 결과 딕셔너리.
            {
                channel_name: [
                    {
                        "filename": 파일명,
                        "path": 저장된 경로,
                        "date": 메시지 날짜
                    },
                    ...
                ],
                ...
            }
        """
        client = await self.client_manager.get_client(self.config)

        # cutoff date 계산
        now = datetime.now(timezone.utc)
        cutoff_date = now - timedelta(days=days)

        results = {}

        for channel in channels:
            channel_results = []

            try:
                # 채널 엔티티 획득
                entity = await client.get_entity(channel)

                # 메시지 반복 조회
                async for message in client.iter_messages(entity):
                    # cutoff date 이전 메시지는 중단
                    if message.date.replace(tzinfo=timezone.utc) < cutoff_date:
                        break

                    # PDF 파일 검사
                    if not self._is_pdf_message(message):
                        continue

                    # 파일명 획득
                    original_filename = self._get_original_filename(message)
                    if not original_filename:
                        original_filename = f"document_{message.id}.pdf"

                    # 통일된 파일명 생성
                    filename = self.pdf_namer.get_filename(original_filename)

                    # 출력 경로 생성
                    output_path = self._get_output_path(
                        channel, filename, message.date
                    )

                    # 디렉토리 생성
                    output_path.parent.mkdir(parents=True, exist_ok=True)

                    # PDF 다운로드
                    await client.download_media(message.media, file=output_path)

                    # 결과 추가
                    channel_results.append(
                        {
                            "filename": filename,
                            "path": str(output_path),
                            "date": message.date.strftime("%Y-%m-%d %H:%M"),
                        }
                    )

            except Exception as e:
                # 채널 오류 처리 (채널 없음, 네트워크 오류 등)
                print(f"채널 '{channel}' 처리 중 오류: {e}")
                channel_results = []

            results[channel] = channel_results

        return results

    def _is_pdf_message(self, message) -> bool:
        """
        메시지가 PDF 파일인지 확인합니다.

        Args:
            message: Telegram 메시지 객체

        Returns:
            PDF 파일이면 True, 아니면 False
        """
        if not message.media:
            return False

        # MessageMediaDocument 타입 확인
        if not isinstance(message.media, MessageMediaDocument):
            return False

        # PDF mime type 확인
        if not hasattr(message.media, "document") or not message.media.document:
            return False

        mime_type = message.media.document.mime_type
        return mime_type == "application/pdf"

    def _get_original_filename(self, message) -> Optional[str]:
        """
        메시지의 원본 파일명을 획득합니다.

        Args:
            message: Telegram 메시지 객체

        Returns:
            파일명, 없으면 None
        """
        if (
            not message.media
            or not message.media.document
            or not hasattr(message.media.document, "attributes")
        ):
            return None

        attributes = message.media.document.attributes
        if not attributes:
            return None

        # 첫 번째 attribute에서 file_name 획득
        first_attr = attributes[0]
        if hasattr(first_attr, "file_name"):
            return first_attr.file_name

        return None

    def _get_output_path(self, channel: str, filename: str, date: datetime) -> Path:
        """
        다운로드한 PDF를 저장할 경로를 생성합니다.

        경로 형식: {output_dir}/reports/{date_str}/{filename}

        Args:
            channel: 채널명
            filename: 파일명
            date: 메시지 날짜

        Returns:
            저장할 파일 경로
        """
        date_str = date.strftime("%Y-%m-%d")
        output_path = (
            self.config.output_dir / "reports" / date_str / filename
        )

        return output_path
