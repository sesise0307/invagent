"""Telegram 인증 관련 함수"""
import sys
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
        print(f"\n✅ 인증 성공! 로그인된 계정: {me.first_name} (@{me.username})")
        print(f"세션 파일 저장 위치: {config.session_path}")
        await client.disconnect()
        return client
    except Exception as e:
        print(f"❌ 인증 실패: {e}")
        # sys.exit() 전에 disconnect 호출
        await client.disconnect()
        sys.exit(1)
