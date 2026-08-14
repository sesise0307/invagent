"""
URL 추출 및 내용 fetch 기능을 담당하는 LinkExtractor 클래스.

이 모듈은 텍스트에서 URL을 추출하고, URL 내용을 가져올 수 있습니다.
"""

import asyncio
import functools
import re
from typing import Optional

try:
    import requests
    from bs4 import BeautifulSoup
    import trafilatura
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

TELEGRAM_URL_PATTERN = re.compile(
    r'https?://t\.me/[^\s\)]*|'  # https://t.me/ 링크
    r'(?<!\S)t\.me/[^\s\)]*',    # t.me/ 링크 (단어 경계)
    re.IGNORECASE
)


class LinkExtractor:
    """URL 추출 및 내용 fetch 기능을 제공하는 클래스."""

    def __init__(self, timeout: int = 10, hard_timeout: Optional[int] = None) -> None:
        """
        LinkExtractor를 초기화합니다.

        Args:
            timeout: URL 요청의 타임아웃 시간 (초). 기본값: 10초
            hard_timeout: URL 1건 처리의 상한(초). trafilatura·BeautifulSoup 단계까지
                포함해 이 시간을 넘기면 강제로 중단한다. 기본값: timeout의 3배
        """
        self.timeout = timeout
        self.hard_timeout = hard_timeout if hard_timeout is not None else timeout * 3
        self._trafilatura_config = self._build_trafilatura_config(timeout)

    @staticmethod
    def _build_trafilatura_config(timeout: int):
        """trafilatura 다운로드에 명시적 타임아웃을 적용한 설정을 만든다."""
        if not HAS_REQUESTS:
            return None
        from copy import deepcopy

        from trafilatura.settings import DEFAULT_CONFIG

        config = deepcopy(DEFAULT_CONFIG)
        config["DEFAULT"]["DOWNLOAD_TIMEOUT"] = str(timeout)
        config["DEFAULT"]["SLEEP_TIME"] = "0"
        config["DEFAULT"]["MAX_REDIRECTS"] = "2"
        return config

    @staticmethod
    def is_telegram_url(url: str) -> bool:
        """URL이 텔레그램 링크인지 확인합니다."""
        return bool(TELEGRAM_URL_PATTERN.match(url))

    def extract_urls(self, text: str) -> list[str]:
        """
        텍스트에서 URL을 추출합니다. 텔레그램 링크는 제외됩니다.

        지원하는 URL 형식:
        - http:// 또는 https://로 시작하는 URL (t.me 제외)
        - www.로 시작하는 URL
        - '링크: '로 시작하는 형태의 URL

        중복은 자동으로 제거됩니다.

        Args:
            text: URL을 추출할 텍스트

        Returns:
            추출된 URL의 리스트 (중복 제거됨, 텔레그램 링크 제외)
        """
        if not text:
            return []

        urls = []
        for match in URL_PATTERN.finditer(text):
            url = match.group(0)
            if url.startswith("링크:"):
                url = url.replace("링크:", "").strip()
            if url and not self.is_telegram_url(url):
                urls.append(url)

        return list(set(urls))  # 중복 제거

    def remove_telegram_urls(self, text: str) -> str:
        """텍스트에서 텔레그램 링크를 제거합니다."""
        return TELEGRAM_URL_PATTERN.sub("", text).strip()

    async def fetch_content(self, url: str) -> str:
        """
        URL의 내용을 가져옵니다.

        trafilatura로 1차 추출하고, 실패 시 BeautifulSoup으로 fallback합니다.

        Args:
            url: 내용을 가져올 URL

        Returns:
            추출된 내용 문자열. 오류 발생 시 오류 메시지를 반환합니다.
        """
        if not HAS_REQUESTS:
            return ""

        try:
            # 1차: trafilatura로 본문 추출
            loop = asyncio.get_running_loop()
            downloaded = await loop.run_in_executor(
                None,
                functools.partial(trafilatura.fetch_url, url, config=self._trafilatura_config),
            )
            if downloaded:
                text = trafilatura.extract(
                    downloaded,
                    include_tables=False,
                    no_fallback=False,
                    include_comments=False,
                )
                if text:
                    stripped = text.strip()
                    if stripped:
                        return stripped[:1500] + ("..." if len(stripped) > 1500 else "")

            # 2차 fallback: BeautifulSoup
            response = await loop.run_in_executor(None, self._fetch_sync, url)

            if response is None:
                return "[링크 읽기 실패]"

            response.encoding = "utf-8"

            if response.status_code != 200:
                return f"[링크 읽기 실패: HTTP {response.status_code}]"

            soup = BeautifulSoup(response.text, "html.parser")

            # 제목 추출 (meta 제거 전에)
            title = ""
            title_tag = soup.find("title")
            if title_tag:
                title = title_tag.get_text(strip=True)
            elif soup.find("h1"):
                title = soup.find("h1").get_text(strip=True)

            # og:description / meta description 추출 (meta 제거 전에)
            content = ""
            og_desc = soup.find("meta", property="og:description")
            if og_desc and og_desc.get("content"):
                content = og_desc["content"].strip()

            if not content:
                meta_desc = soup.find("meta", attrs={"name": "description"})
                if meta_desc and meta_desc.get("content"):
                    content = meta_desc["content"].strip()

            # 이제 불필요한 태그 제거
            for tag in soup(["script", "style", "meta", "noscript"]):
                tag.decompose()

            # paragraph 추출
            if not content:
                paragraphs = soup.find_all("p", limit=5)
                if paragraphs:
                    content = "\n".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))

            # article 본문
            if not content:
                article = soup.find("article")
                if article:
                    content = article.get_text(separator="\n", strip=True)

            # 전체 텍스트
            if not content:
                raw = soup.get_text(separator="\n", strip=True)
                content = "\n".join(line for line in raw.split("\n") if line.strip())

            result = ""
            if title:
                result = f"**{title}**"
            if content:
                if result:
                    result += "\n"
                result += content[:1500] + ("..." if len(content) > 1500 else "")

            return result if result else "[내용을 읽을 수 없습니다]"

        except requests.exceptions.Timeout:
            return "[링크 읽기 타임아웃]"
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

    async def _fetch_content_bounded(self, url: str) -> str:
        """`fetch_content`를 hard_timeout으로 감싼다.

        trafilatura·BeautifulSoup 단계가 자체 타임아웃을 지키지 못하고 멈추면
        수집 전체가 무기한 대기 상태가 되므로, URL 1건마다 상한을 강제한다.
        """
        try:
            return await asyncio.wait_for(self.fetch_content(url), timeout=self.hard_timeout)
        except asyncio.TimeoutError:
            return "[링크 읽기 타임아웃]"

    async def extract_and_fetch(self, text: str) -> dict[str, list[str] | str]:
        """
        텍스트에서 URL을 추출하고 각 URL의 내용을 병렬로 가져옵니다.

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

        results = await asyncio.gather(
            *[self._fetch_content_bounded(url) for url in urls],
            return_exceptions=True,
        )

        contents = {}
        for url, result in zip(urls, results):
            if isinstance(result, Exception):
                contents[url] = f"[링크 읽기 오류: {str(result)[:50]}]"
            else:
                contents[url] = result

        return {"urls": urls, "contents": contents}
