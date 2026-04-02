"""Telegram 클라이언트 관리"""
from typing import Optional
from telethon import TelegramClient
from .config import Config


class TelegramClientManager:
    """TelegramClient 싱글톤 관리"""

    def __init__(self):
        self._client: Optional[TelegramClient] = None
        self._config: Optional[Config] = None

    async def get_client(self, config: Config) -> TelegramClient:
        """Telegram 클라이언트 획득 (싱글톤)

        처음 호출 시 클라이언트 생성 및 연결.
        이후 호출 시 기존 클라이언트 반환.
        """
        if self._client is None:
            self._config = config
            self._client = TelegramClient(
                str(config.session_path),
                config.api_id,
                config.api_hash
            )
            await self._client.connect()

        return self._client

    async def disconnect(self):
        """클라이언트 연결 해제"""
        if self._client:
            await self._client.disconnect()
            self._client = None
