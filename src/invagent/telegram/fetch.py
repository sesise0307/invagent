"""
저장된 메시지 조회 및 포맷팅 기능을 담당하는 MessageFetcher 클래스.

이 모듈은 Telegram의 "저장된 메시지" 채널에서 메시지를 조회하고
링크 내용과 첨부 이미지를 수집한 후 포맷팅할 수 있습니다.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto

from invagent.core.config import Config
from invagent.core.client import TelegramClientManager
from invagent.telegram.link_extractor import LinkExtractor


# 이미지 판정/다운로드 한계값. 링크 추출기의 MAX_RESPONSE_BYTES와 같은 성격의
# 방어선으로, 저장된 메시지는 신뢰할 수 없는 입력이므로 한 번의 실행이 소비할 수
# 있는 디스크와 판독 예산을 미리 못 박는다.
IMAGE_MIME_PREFIX = "image/"
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGES_PER_RUN = 60

# raw 마크다운에 남는 판독 대기 마커. summarize-telegram 스킬 1-4단계가 이 문자열을
# 찾아 판독 결과로 치환하므로 코드와 SKILL.md 양쪽의 계약이다. 한쪽만 바꾸면 스킬이
# 이미지를 하나도 못 찾는다.
PENDING_IMAGE_MARKER = "[분석 대기]"


def _is_image_message(message) -> bool:
    """메시지에 판독 가능한 이미지가 붙어 있는지 판정합니다.

    화이트리스트 방식이다. 링크 미리보기(MessageMediaWebPage)나 영상·음성·PDF가
    이미지로 잡히면 안 되므로, 사진이거나 mime type이 image/인 문서일 때만 참이다.
    """
    media = getattr(message, "media", None)
    if media is None:
        return False

    if isinstance(media, MessageMediaPhoto):
        return True

    if isinstance(media, MessageMediaDocument):
        document = getattr(media, "document", None)
        mime_type = getattr(document, "mime_type", None) or ""
        return mime_type.startswith(IMAGE_MIME_PREFIX)

    return False


class MessageFetcher:
    """저장된 메시지 조회 및 포맷팅 기능을 제공하는 클래스."""

    def __init__(self, config: Config, client_manager: TelegramClientManager) -> None:
        """
        MessageFetcher를 초기화합니다.

        Args:
            config: Config 인스턴스
            client_manager: TelegramClientManager 인스턴스
        """
        self.config = config
        self.client_manager = client_manager
        self.link_extractor = LinkExtractor()

    async def fetch_saved_messages(
        self,
        days: int = 1,
        fetch_links: bool = False,
        media_dir: Optional[Path] = None,
    ) -> list[dict]:
        """
        저장된 메시지를 조회합니다.

        "me" (저장된 메시지) 채널에서 지정된 기간 내의 메시지를 조회합니다.
        cutoff date 이전 메시지는 조회를 중단합니다.

        Args:
            days: 조회할 기간 (일 단위). 기본값: 1
            fetch_links: True일 경우 링크 내용을 추출. 기본값: False
            media_dir: 첨부 이미지를 내려받을 디렉토리. None이면 다운로드하지 않고
                이미지 존재 여부만 기록한다.

        Returns:
            메시지 리스트. 각 메시지는 다음 구조의 딕셔너리:
            {
                "id": 메시지 ID,
                "date": "YYYY-MM-DD HH:MM" 형식의 날짜,
                "text": 메시지 본문 (이미지 전용 메시지는 빈 문자열),
                "links_content": 링크 내용 (fetch_links=True일 때만),
                "is_forwarded": 포워드된 메시지 여부,
                "images": 저장된 이미지 경로 또는 실패 센티널 문자열 리스트
            }
        """
        client = await self.client_manager.get_client(self.config)

        # cutoff date 계산
        now = datetime.now(timezone.utc)
        cutoff_date = now - timedelta(days=days)

        messages = []
        downloaded_images = 0

        # 저장된 메시지("me" 채널)에서 메시지 조회
        async for message in client.iter_messages("me"):
            # cutoff date 이전 메시지는 중단
            if message.date.replace(tzinfo=timezone.utc) < cutoff_date:
                break

            has_image = _is_image_message(message)

            # 텍스트도 이미지도 없으면 스킵
            if not message.text and not has_image:
                continue

            text = self.link_extractor.remove_telegram_urls(message.text or "")
            # 텍스트가 링크뿐이라 비었더라도 이미지가 있으면 살린다
            if not text.strip() and not has_image:
                continue

            msg_dict = {
                "id": message.id,
                "date": message.date.strftime("%Y-%m-%d %H:%M"),
                "text": text,
                "links_content": "",
                "is_forwarded": message.forward is not None,
                "images": [],
            }

            # 이미지 다운로드
            if has_image and media_dir is not None:
                saved, consumed = await self._download_image(
                    client, message, media_dir, downloaded_images
                )
                msg_dict["images"].append(saved)
                downloaded_images += consumed

            # 링크 내용 추출
            if fetch_links:
                result = await self.link_extractor.extract_and_fetch(text)
                links_contents = []
                for url, content in result.get("contents", {}).items():
                    if content:
                        links_contents.append(f"URL: {url}\n{content}")

                msg_dict["links_content"] = "\n\n".join(links_contents)

            messages.append(msg_dict)

        return messages

    async def _download_image(
        self, client, message, media_dir: Path, downloaded_so_far: int
    ) -> tuple[str, int]:
        """
        메시지의 이미지를 media_dir에 저장합니다.

        실패해도 예외를 올리지 않습니다. 이미지 하나 때문에 그날 브리핑 전체가
        날아가면 안 되므로, LinkExtractor.fetch_content와 같이 대괄호 센티널
        문자열을 돌려주고 호출자는 그대로 raw 파일에 남깁니다.

        Args:
            client: Telethon 클라이언트
            message: 대상 메시지
            media_dir: 저장 디렉토리
            downloaded_so_far: 이번 실행에서 이미 내려받은 이미지 수

        Returns:
            (저장 경로 또는 센티널 문자열, 다운로드를 실제로 소비했으면 1 아니면 0)
        """
        if downloaded_so_far >= MAX_IMAGES_PER_RUN:
            return f"[이미지 건너뜀: 실행당 상한 {MAX_IMAGES_PER_RUN}장 초과]", 0

        size = getattr(getattr(message, "file", None), "size", None)
        if isinstance(size, int) and size > MAX_IMAGE_BYTES:
            return f"[이미지 건너뜀: 용량 초과 {size}바이트]", 0

        try:
            media_dir.mkdir(parents=True, exist_ok=True)
            # 확장자 없는 경로를 주면 telethon이 mime에 맞는 확장자를 붙이고
            # 실제 저장 경로를 돌려준다.
            saved = await client.download_media(
                message.media, file=str(media_dir / str(message.id))
            )
        except Exception as e:  # noqa: BLE001 - 이미지 실패가 실행을 막으면 안 된다
            return f"[이미지 저장 실패: {str(e)[:50]}]", 0

        if not saved:
            return "[이미지 저장 실패: 저장 경로 없음]", 0

        return str(saved), 1

    @staticmethod
    def _image_lines(images: list[str]) -> list[str]:
        """이미지 블록을 blockquote 줄 리스트로 만듭니다.

        저장에 성공한 항목만 판독 대기 마커를 붙입니다. 실패 센티널은 판독할
        파일 자체가 없으므로 마커 없이 사유만 남깁니다.
        """
        lines = ["이미지:"]
        for image in images:
            if image.startswith("["):
                lines.append(f"> {image}")
            else:
                lines.append(f"> 파일: {image}")
                lines.append(f"> {PENDING_IMAGE_MARKER}")
        return lines

    def format_messages(self, messages: list[dict]) -> str:
        """
        메시지 리스트를 포맷팅된 문자열로 변환합니다.

        메시지는 오래된 순으로 출력됩니다.

        Args:
            messages: MessageFetcher.fetch_saved_messages에서 반환된 메시지 리스트

        Returns:
            포맷팅된 메시지 문자열
        """
        # 오래된 순으로 정렬 (역순)
        sorted_messages = sorted(messages, key=lambda m: m["id"])

        lines = []
        for msg in sorted_messages:
            lines.append(f"[{msg['date']}] (ID: {msg['id']})")
            if msg["text"]:
                lines.append(msg["text"])

            if msg.get("images"):
                lines.extend(self._image_lines(msg["images"]))

            if msg.get("links_content"):
                lines.append("\n--- 링크 내용 ---")
                lines.append(msg["links_content"])

            lines.append("")  # 메시지 사이의 빈 줄

        return "\n".join(lines)

    def format_messages_markdown(self, messages: list[dict]) -> str:
        """
        메시지 리스트를 마크다운 형식으로 변환합니다.

        포워드 여부에 따라 "내 메시지" 또는 "포워드"로 구분합니다.
        메시지는 오래된 순으로 출력됩니다.

        Args:
            messages: fetch_saved_messages에서 반환된 메시지 리스트 (is_forwarded 필드 필수)

        Returns:
            마크다운 포맷팅된 메시지 문자열
        """
        # 오래된 순으로 정렬 (역순)
        sorted_messages = sorted(messages, key=lambda m: m["id"])

        lines = []
        for msg in sorted_messages:
            # 레이블 결정
            label = "포워드" if msg.get("is_forwarded", False) else "내 메시지"

            # 헤더: **[레이블]** 날짜
            lines.append(f"**[{label}]** {msg['date']}")

            # 메시지 본문 (이미지 전용 메시지는 빈 줄을 만들지 않는다)
            if msg["text"]:
                lines.append(msg["text"])

            # 이미지 (있으면)
            if msg.get("images"):
                lines.append("")  # 빈 줄
                lines.extend(self._image_lines(msg["images"]))

            # 링크 내용 (있으면)
            if msg.get("links_content"):
                lines.append("")  # 빈 줄
                lines.append("링크:")
                # 링크 내용을 blockquote로 감싸기
                for link_line in msg["links_content"].split("\n"):
                    lines.append(f"> {link_line}")

            lines.append("")  # 메시지 사이의 빈 줄

        return "\n".join(lines)
