#!/usr/bin/env python3
"""
텔레그램 채널에서 PDF 파일만 다운로드합니다.
저장 경로: outputs/reports/<yyyy-mm-dd>/
사용법: python3 download_telegram_pdfs.py [--days 1] [--channels 채널1 채널2 ...]
"""

import asyncio
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Telethon
try:
    from telethon import TelegramClient
    from telethon.tl.types import MessageMediaDocument
except ImportError:
    print("ERROR: telethon이 설치되지 않았습니다. pip3 install telethon 실행 후 다시 시도하세요.")
    sys.exit(1)

# ── 자격증명 설정 ──────────────────────────────────────────────
API_ID   = int(os.environ.get("TELEGRAM_API_ID", "0"))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
SESSION  = os.path.expanduser("~/.telegram_session")
# ──────────────────────────────────────────────────────────────

# 기본 채널 목록
DEFAULT_CHANNELS = [
    "소중한추억.",
    "선진짱 주식공부방",
    "리포트 갤러리",
    "영리한타이거의 주식공부방",
]


async def download_pdfs_from_channels(channels: list[str], days: int) -> dict:
    """
    지정된 채널들에서 PDF를 찾아 다운로드합니다.
    반환: {channel_name: [(filename, path), ...], ...}
    """
    if not API_ID or not API_HASH:
        print("ERROR: TELEGRAM_API_ID / TELEGRAM_API_HASH 환경변수를 설정해 주세요.")
        sys.exit(1)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    results = {}

    async with TelegramClient(SESSION, API_ID, API_HASH) as client:
        for channel_name in channels:
            print(f"\n📡 [{channel_name}] 검색 중...")
            channel_results = []

            try:
                # 채널 entity 조회
                entity = await client.get_entity(channel_name)
                print(f"   ✓ 채널 발견")

            except ValueError as e:
                print(f"   ✗ 채널을 찾을 수 없습니다: {e}")
                continue
            except Exception as e:
                print(f"   ✗ 오류: {e}")
                continue

            try:
                # 최근 메시지 스캔 (최대 500개)
                msg_count = 0
                pdf_count = 0

                async for msg in client.iter_messages(entity, limit=500):
                    msg_count += 1

                    # 날짜 필터
                    if msg.date < cutoff:
                        print(f"   → {msg_count}개 메시지 검색 (cutoff 도달, {pdf_count}개 PDF 발견)")
                        break

                    # PDF 필터
                    if not isinstance(msg.media, MessageMediaDocument):
                        continue

                    doc = msg.media.document
                    if doc.mime_type != "application/pdf":
                        continue

                    # PDF 발견!
                    pdf_count += 1
                    filename = doc.attributes[0].file_name if doc.attributes else f"document_{msg.id}.pdf"
                    date_str = msg.date.astimezone().strftime("%Y-%m-%d")

                    # 출력 디렉토리 생성
                    out_dir = Path("outputs/reports") / date_str
                    out_dir.mkdir(parents=True, exist_ok=True)

                    out_path = out_dir / filename

                    # 파일 다운로드
                    print(f"   📥 {filename} ({date_str})...")
                    await client.download_media(msg, file=out_path)

                    channel_results.append({
                        "filename": filename,
                        "path": str(out_path),
                        "date": date_str,
                    })

                if msg_count == 500:
                    print(f"   → 최대 500개 메시지 검색 완료 ({pdf_count}개 PDF 발견)")

            except Exception as e:
                print(f"   ✗ 처리 중 오류: {e}")
                continue

            results[channel_name] = channel_results

    return results


def print_summary(results: dict):
    """다운로드 결과 요약"""
    print("\n" + "=" * 60)
    print("📊 다운로드 완료")
    print("=" * 60)

    total_pdfs = 0
    for channel_name, files in results.items():
        count = len(files)
        total_pdfs += count
        status = "✓" if count > 0 else "✗"
        print(f"\n{status} [{channel_name}] {count}개 PDF")

        for f in files:
            print(f"   → {f['filename']} ({f['date']})")
            print(f"      {f['path']}")

    print("\n" + "=" * 60)
    print(f"총 {total_pdfs}개 PDF 다운로드 완료")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="텔레그램 채널에서 PDF 다운로드",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  # 기본 채널에서 최근 1일치 PDF 다운로드
  python3 download_telegram_pdfs.py

  # 최근 7일치 검색
  python3 download_telegram_pdfs.py --days 7

  # 특정 채널만 검색
  python3 download_telegram_pdfs.py --channels "리포트 갤러리" "소중한추억."
        """
    )
    parser.add_argument("--days", type=int, default=1, help="최근 N일치 메시지 (기본: 1)")
    parser.add_argument(
        "--channels",
        nargs="+",
        default=DEFAULT_CHANNELS,
        help=f"검색할 채널 이름 (기본: {', '.join(DEFAULT_CHANNELS)})"
    )
    args = parser.parse_args()

    print("🔧 텔레그램 PDF 다운로더")
    print(f"   기간: 최근 {args.days}일")
    print(f"   채널: {len(args.channels)}개")

    results = asyncio.run(download_pdfs_from_channels(args.channels, args.days))
    print_summary(results)
