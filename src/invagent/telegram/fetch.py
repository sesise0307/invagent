"""
저장된 메시지 조회 및 포맷팅 기능을 담당하는 MessageFetcher 클래스.

이 모듈은 Telegram의 "저장된 메시지" 채널에서 메시지를 조회하고
링크 내용을 추출한 후 포맷팅할 수 있습니다.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from invagent.core.config import Config
from invagent.core.client import TelegramClientManager
from invagent.telegram.link_extractor import LinkExtractor


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
        self, days: int = 1, fetch_links: bool = False
    ) -> list[dict]:
        """
        저장된 메시지를 조회합니다.

        "me" (저장된 메시지) 채널에서 지정된 기간 내의 메시지를 조회합니다.
        cutoff date 이전 메시지는 조회를 중단합니다.

        Args:
            days: 조회할 기간 (일 단위). 기본값: 1
            fetch_links: True일 경우 링크 내용을 추출. 기본값: False

        Returns:
            메시지 리스트. 각 메시지는 다음 구조의 딕셔너리:
            {
                "id": 메시지 ID,
                "date": "YYYY-MM-DD HH:MM" 형식의 날짜,
                "text": 메시지 본문,
                "links_content": 링크 내용 (fetch_links=True일 때만),
                "is_forwarded": 포워드된 메시지 여부
            }
        """
        client = await self.client_manager.get_client(self.config)

        # cutoff date 계산
        now = datetime.now(timezone.utc)
        cutoff_date = now - timedelta(days=days)

        messages = []

        # 저장된 메시지("me" 채널)에서 메시지 조회
        async for message in client.iter_messages("me"):
            # cutoff date 이전 메시지는 중단
            if message.date.replace(tzinfo=timezone.utc) < cutoff_date:
                break

            # 메시지 텍스트가 없으면 스킵
            if not message.text:
                continue

            text = self.link_extractor.remove_telegram_urls(message.text)

            msg_dict = {
                "id": message.id,
                "date": message.date.strftime("%Y-%m-%d %H:%M"),
                "text": text,
                "links_content": "",
                "is_forwarded": message.forward is not None,
            }

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
            lines.append(msg["text"])

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

            # 메시지 본문
            lines.append(msg["text"])

            # 링크 내용 (있으면)
            if msg.get("links_content"):
                lines.append("")  # 빈 줄
                lines.append("링크:")
                # 링크 내용을 blockquote로 감싸기
                for link_line in msg["links_content"].split("\n"):
                    lines.append(f"> {link_line}")

            lines.append("")  # 메시지 사이의 빈 줄

        return "\n".join(lines)
