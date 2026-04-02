"""
URL 추출 및 내용 fetch 기능을 담당하는 LinkExtractor 클래스.

이 모듈은 텍스트에서 URL을 추출하고, URL 내용을 가져올 수 있습니다.
"""

import asyncio
import re
import sys
from typing import Optional

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    HAS_REQUESTS = False
else:
    HAS_REQUESTS = True


# URL 정규표현식 (fetch_telegram.py와 동일)
URL_PATTERN = re.compile(
    r'https?://[^\s\)]+|'  # http/https URL
    r'www\.[^\s\)]+|'       # www URL
    r't\.me/[^\s\)]+|'      # telegram 링크
    r'링크:\s*([^\s\)]+)',  # "링크: " 형태
    re.IGNORECASE
)


class LinkExtractor:
    """URL 추출 및 내용 fetch 기능을 제공하는 클래스."""

    def __init__(self, timeout: int = 5) -> None:
        """
        LinkExtractor를 초기화합니다.

        Args:
            timeout: URL 요청의 타임아웃 시간 (초). 기본값: 5초
        """
        self.timeout = timeout

    def extract_urls(self, text: str) -> list[str]:
        """
        텍스트에서 URL을 추출합니다.

        지원하는 URL 형식:
        - http:// 또는 https://로 시작하는 URL
        - www.로 시작하는 URL
        - t.me/로 시작하는 Telegram 링크
        - '링크: '로 시작하는 형태의 URL

        중복은 자동으로 제거됩니다.

        Args:
            text: URL을 추출할 텍스트

        Returns:
            추출된 URL의 리스트 (중복 제거됨)
        """
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

    async def fetch_content(self, url: str) -> str:
        """
        URL의 내용을 가져옵니다.

        BeautifulSoup을 사용하여 HTML을 파싱하고 핵심 텍스트를 추출합니다.
        추출 순서:
        1. 제목 (title 또는 h1)
        2. Open Graph description 또는 meta description
        3. 첫 번째 paragraph
        4. article 요소
        5. 전체 텍스트

        Args:
            url: 내용을 가져올 URL

        Returns:
            추출된 내용 문자열. 오류 발생 시 오류 메시지를 반환합니다.
        """
        if not HAS_REQUESTS:
            return ""

        try:
            # 비동기 루프에서 동기 함수를 실행
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                self._fetch_sync,
                url
            )

            if response is None:
                return "[링크 읽기 실패]"

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

            # 본문 추출
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
                content = '\n'.join(
                    line.strip() for line in text.split('\n') if line.strip()
                )[:500]

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

    def _fetch_sync(self, url: str) -> Optional["requests.Response"]:
        """
        동기 HTTP GET 요청을 수행합니다.

        이 메서드는 asyncio.run_in_executor에서 호출됩니다.

        Args:
            url: 요청할 URL

        Returns:
            requests.Response 객체 또는 None
        """
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                         'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        return requests.get(
            url,
            headers=headers,
            timeout=self.timeout,
            allow_redirects=True
        )

    async def extract_and_fetch(self, text: str) -> dict[str, list[str] | str]:
        """
        텍스트에서 URL을 추출하고 각 URL의 내용을 가져옵니다.

        Args:
            text: 처리할 텍스트

        Returns:
            다음 구조의 딕셔너리:
            {
                "urls": [추출된 URL 리스트],
                "contents": {URL: 추출된 내용}
            }
        """
        urls = self.extract_urls(text)
        contents = {}

        for url in urls:
            content = await self.fetch_content(url)
            contents[url] = content

        return {
            "urls": urls,
            "contents": contents
        }
