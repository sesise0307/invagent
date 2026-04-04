"""Telegram 인증 관련 함수"""

from telethon import TelegramClient
from .config import Config


async def authenticate(config: Config) -> TelegramClient:
    """Telegram 인증 (1회만 실행)

    실행 후 ~/.telegram_session 파일이 생성되면 완료.
    """
    client = TelegramClient(str(config.session_path), config.api_id, config.api_hash)

    try:
        await client.start()  # 대화식 인증 진행
        me = await client.get_me()
        return client
    except Exception as e:
        await client.disconnect()
        raise RuntimeError(f"인증 실패: {e}") from e
