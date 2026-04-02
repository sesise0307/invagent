#!/usr/bin/env python3
"""
텔레그램 최초 인증 스크립트 (1회만 실행)
실행 후 ~/.telegram_session 파일이 생성되면 완료.
"""

import asyncio
import os
import sys

try:
    from telethon import TelegramClient
except ImportError:
    print("ERROR: pip3 install telethon")
    sys.exit(1)

API_ID   = int(os.environ.get("TELEGRAM_API_ID", "0"))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
SESSION  = os.path.expanduser("~/.telegram_session")

if not API_ID or not API_HASH:
    print("ERROR: TELEGRAM_API_ID / TELEGRAM_API_HASH 환경변수를 설정해 주세요.")
    sys.exit(1)

async def main():
    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.start()  # 대화식 인증 진행
    me = await client.get_me()
    print(f"\n✅ 인증 성공! 로그인된 계정: {me.first_name} (@{me.username})")
    print(f"세션 파일 저장 위치: {SESSION}")
    await client.disconnect()

asyncio.run(main())
