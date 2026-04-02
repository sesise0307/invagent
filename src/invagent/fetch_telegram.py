#!/usr/bin/env python3
"""
텔레그램 '저장한 메시지' 채널에서 최근 메시지를 읽어 출력합니다.
링크가 포함된 메시지는 자동으로 링크 내용을 추출하여 함께 정리합니다.
사용법: python3 fetch_telegram.py [--days 1] [--fetch-links]
"""

import asyncio
import argparse
import os
import sys
import re
from datetime import datetime, timedelta, timezone

# Telethon
try:
    from telethon import TelegramClient
    from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
except ImportError:
    print("ERROR: telethon이 설치되지 않았습니다. pip3 install telethon 실행 후 다시 시도하세요.")
    sys.exit(1)

# 웹 크롤링
try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("WARNING: requests 또는 beautifulsoup4가 설치되지 않았습니다.")
    print("링크 내용 추출 기능이 비활성화됩니다.")
    print("설치: pip3 install requests beautifulsoup4")
    HAS_REQUESTS = False
else:
    HAS_REQUESTS = True

# ── 자격증명 설정 ──────────────────────────────────────────────
# 환경변수로 주입하거나, 아래에 직접 입력하세요.
API_ID   = int(os.environ.get("TELEGRAM_API_ID", "0"))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
SESSION  = os.path.expanduser("~/.telegram_session")
# ──────────────────────────────────────────────────────────────

# URL 정규표현식
URL_PATTERN = re.compile(
    r'https?://[^\s\)]+|'  # http/https URL
    r'www\.[^\s\)]+|'       # www URL
    r't\.me/[^\s\)]+|'      # telegram 링크
    r'링크:\s*([^\s\)]+)',  # "링크: " 형태
    re.IGNORECASE
)


def extract_urls(text: str) -> list[str]:
    """텍스트에서 URL 추출"""
    if not text:
        return []

    urls = []
    for match in URL_PATTERN.finditer(text):
        url = match.group(0)
        if url.startswith("링크:"):
            url = url.replace("링크:", "").strip()
        if url:
            urls.append(url)

    return list(set(urls))  # 중복 제거


def fetch_url_content(url: str, timeout: int = 5) -> str:
    """URL 내용을 가져와서 핵심 텍스트 추출"""
    if not HAS_REQUESTS:
        return ""

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                         'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

        response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        response.encoding = 'utf-8'

        if response.status_code != 200:
            return f"[링크 읽기 실패: HTTP {response.status_code}]"

        soup = BeautifulSoup(response.text, 'html.parser')

        # 스크립트, 스타일, 메타 제거
        for script in soup(["script", "style", "meta", "noscript"]):
            script.decompose()

        # 제목 추출
        title = ""
        if soup.find('title'):
            title = soup.find('title').get_text(strip=True)
        elif soup.find('h1'):
            title = soup.find('h1').get_text(strip=True)

        # 본문 추출 (og:description, meta description, 또는 첫 paragraph)
        content = ""

        # Open Graph description
        og_desc = soup.find('meta', property='og:description')
        if og_desc and og_desc.get('content'):
            content = og_desc['content'].strip()

        # Meta description
        if not content:
            meta_desc = soup.find('meta', attrs={'name': 'description'})
            if meta_desc and meta_desc.get('content'):
                content = meta_desc['content'].strip()

        # 첫 번째 paragraph
        if not content:
            paragraphs = soup.find_all('p', limit=2)
            if paragraphs:
                content = '\n'.join([p.get_text(strip=True) for p in paragraphs])

        # 기사 본문 (news sites)
        if not content:
            article = soup.find('article')
            if article:
                content = article.get_text(separator='\n', strip=True)[:500]

        # 최종 텍스트 추출
        if not content:
            text = soup.get_text(separator='\n', strip=True)
            content = '\n'.join(line.strip() for line in text.split('\n') if line.strip())[:500]

        # 결과 포맷팅
        result = ""
        if title:
            result = f"📄 **{title}**"

        if content:
            if result:
                result += "\n"
            result += content[:300] + ("..." if len(content) > 300 else "")

        return result if result else "[내용을 읽을 수 없습니다]"

    except requests.exceptions.Timeout:
        return "[링크 읽기 타임아웃 (5초)]"
    except requests.exceptions.ConnectionError:
        return "[연결 실패]"
    except Exception as e:
        return f"[링크 읽기 오류: {str(e)[:50]}]"


async def fetch_saved_messages(days: int, fetch_links: bool = False) -> list[dict]:
    if not API_ID or not API_HASH:
        print("ERROR: TELEGRAM_API_ID / TELEGRAM_API_HASH 환경변수를 설정해 주세요.")
        sys.exit(1)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    messages = []

    async with TelegramClient(SESSION, API_ID, API_HASH) as client:
        async for msg in client.iter_messages("me", limit=200):
            if msg.date < cutoff:
                break
            if not msg.text:
                continue

            text = msg.text.strip()
            links_content = ""

            # 링크 추출 및 내용 가져오기
            if fetch_links and HAS_REQUESTS:
                urls = extract_urls(text)
                if urls:
                    links_content = "\n\n"
                    links_content += "━━━ 🔗 링크 내용 ━━━\n"
                    for url in urls:
                        links_content += f"\n📌 {url}\n"
                        content = fetch_url_content(url)
                        if content:
                            links_content += f"{content}\n"
                    links_content += "━━━━━━━━━━━━━━━━\n"

            messages.append({
                "id":   msg.id,
                "date": msg.date.astimezone().strftime("%Y-%m-%d %H:%M"),
                "text": text,
                "links_content": links_content,
            })

    return messages


def print_messages(messages: list[dict]):
    if not messages:
        print("해당 기간 내 저장된 텍스트 메시지가 없습니다.")
        return

    print(f"=== 저장한 메시지 ({len(messages)}건) ===\n")
    for m in reversed(messages):  # 오래된 순으로 출력
        print(f"[{m['date']}]")
        print(m["text"])
        if m.get("links_content"):
            print(m["links_content"])
        print("---")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="텔레그램 저장한 메시지 조회")
    parser.add_argument("--days", type=int, default=1, help="최근 N일치 메시지 (기본: 1)")
    parser.add_argument("--fetch-links", action="store_true", help="링크 내용 자동 추출 (느림)")
    args = parser.parse_args()

    if args.fetch_links and not HAS_REQUESTS:
        print("ERROR: --fetch-links 옵션은 requests 패키지가 필요합니다.")
        print("설치: pip3 install requests beautifulsoup4")
        sys.exit(1)

    result = asyncio.run(fetch_saved_messages(args.days, fetch_links=args.fetch_links))
    print_messages(result)
