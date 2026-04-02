# invagent 패키지 현대화 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 코드 구조를 계층화하고, 중복을 제거하고, 파일명 규칙을 통일하고, 주가 추적 기능을 추가합니다.

**Architecture:** Core 기반 모듈 → Telegram 연동 모듈 → 파일명 규칙화 → 주가 추적 → CLI 통합. 각 모듈은 독립적으로 테스트 가능하고, 새 기능 추가 시 기존 코드 건드릴 필요 없습니다.

**Tech Stack:** Python 3.12+, `click` (CLI), `telethon` (Telegram), `pydantic` (설정 검증 선택사항)

---

## 파일 구조

**생성 파일:**
```
src/invagent/
├── core/
│   ├── __init__.py
│   ├── config.py          # 새로 생성
│   ├── auth.py            # 새로 생성
│   └── client.py          # 새로 생성
├── telegram/
│   ├── __init__.py        # 새로 생성
│   ├── link_extractor.py  # 새로 생성
│   ├── fetch.py           # 새로 생성
│   └── downloader.py      # 새로 생성
├── parsers/
│   ├── __init__.py        # 새로 생성
│   └── pdf_namer.py       # 새로 생성
├── tracking/
│   ├── __init__.py        # 새로 생성
│   └── stock_tracker.py   # 새로 생성
└── cli.py                 # 새로 생성
```

**수정/삭제 파일:**
```
src/invagent/
├── __init__.py            # 수정: 공개 API 정의
├── auth_telegram.py       # 삭제 (core/auth.py로 이동)
├── fetch_telegram.py      # 삭제 (telegram/*로 분리)
└── download_telegram_pdfs.py  # 삭제 (telegram/downloader.py로 이동)
```

**테스트 파일:**
```
tests/
├── test_core_config.py
├── test_core_client.py
├── test_telegram_link_extractor.py
├── test_telegram_fetch.py
├── test_telegram_downloader.py
├── test_parsers_pdf_namer.py
├── test_tracking_stock_tracker.py
└── test_cli.py
```

---

## Phase 1: Core 기반 모듈

### Task 1: `core/config.py` 작성

**Files:**
- Create: `src/invagent/core/config.py`
- Create: `tests/test_core_config.py`

- [ ] **Step 1: 테스트 작성 (환경변수 로드)**

```python
# tests/test_core_config.py
import os
from pathlib import Path
from invagent.core.config import Config

def test_config_from_env_with_valid_env_vars(monkeypatch):
    """환경변수에서 설정을 정상적으로 로드"""
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123def456")
    
    config = Config.from_env()
    
    assert config.api_id == 12345
    assert config.api_hash == "abc123def456"
    assert config.session_path == Path.home() / ".telegram_session"
    assert config.output_dir == Path("outputs")


def test_config_from_env_missing_api_id(monkeypatch):
    """TELEGRAM_API_ID 누락 시 ValueError 발생"""
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123")
    
    try:
        Config.from_env()
        assert False, "ValueError should be raised"
    except ValueError as e:
        assert "TELEGRAM_API_ID" in str(e)


def test_config_from_env_missing_api_hash(monkeypatch):
    """TELEGRAM_API_HASH 누락 시 ValueError 발생"""
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
    
    try:
        Config.from_env()
        assert False, "ValueError should be raised"
    except ValueError as e:
        assert "TELEGRAM_API_HASH" in str(e)
```

- [ ] **Step 2: 테스트 실행 (실패 확인)**

```bash
cd /Users/sesise/1_Investment/invagent
pytest tests/test_core_config.py -v
```

Expected: 3개 테스트 모두 FAIL (모듈 없음)

- [ ] **Step 3: `core/config.py` 구현**

```python
# src/invagent/core/config.py
import os
from pathlib import Path
from dataclasses import dataclass

@dataclass
class Config:
    """Telegram API 설정 및 기타 설정"""
    api_id: int
    api_hash: str
    session_path: Path
    output_dir: Path

    @classmethod
    def from_env(cls) -> "Config":
        """환경변수에서 설정 로드"""
        api_id_str = os.environ.get("TELEGRAM_API_ID", "").strip()
        api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
        
        if not api_id_str:
            raise ValueError("TELEGRAM_API_ID 환경변수가 설정되지 않았습니다.")
        if not api_hash:
            raise ValueError("TELEGRAM_API_HASH 환경변수가 설정되지 않았습니다.")
        
        try:
            api_id = int(api_id_str)
        except ValueError:
            raise ValueError(f"TELEGRAM_API_ID는 정수여야 합니다: {api_id_str}")
        
        session_path = Path.home() / ".telegram_session"
        output_dir = Path("outputs")
        
        return cls(
            api_id=api_id,
            api_hash=api_hash,
            session_path=session_path,
            output_dir=output_dir,
        )
```

- [ ] **Step 4: 테스트 실행 (통과 확인)**

```bash
pytest tests/test_core_config.py -v
```

Expected: PASSED (3/3)

- [ ] **Step 5: `core/__init__.py` 작성**

```python
# src/invagent/core/__init__.py
from .config import Config
from .auth import authenticate
from .client import TelegramClientManager

__all__ = ["Config", "authenticate", "TelegramClientManager"]
```

- [ ] **Step 6: 커밋**

```bash
git add src/invagent/core/__init__.py src/invagent/core/config.py tests/test_core_config.py
git commit -m "feat: add Config class for centralized settings"
```

---

### Task 2: `core/auth.py` 작성

**Files:**
- Create: `src/invagent/core/auth.py`
- Create: `tests/test_core_auth.py`

- [ ] **Step 1: 테스트 작성**

```python
# tests/test_core_auth.py
import pytest
from unittest.mock import AsyncMock, patch
from pathlib import Path
from invagent.core.config import Config
from invagent.core.auth import authenticate


@pytest.mark.asyncio
async def test_authenticate_creates_session_file(tmp_path):
    """authenticate 호출 후 세션 파일 생성 여부 확인"""
    config = Config(
        api_id=123,
        api_hash="test_hash",
        session_path=tmp_path / ".telegram_session",
        output_dir=tmp_path / "outputs",
    )
    
    with patch("invagent.core.auth.TelegramClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client
        mock_client.get_me = AsyncMock(return_value=AsyncMock(first_name="Test", username="testuser"))
        
        client = await authenticate(config)
        
        # TelegramClient가 올바른 파라미터로 초기화됨
        mock_client_class.assert_called_once_with(
            str(config.session_path),
            config.api_id,
            config.api_hash
        )
        mock_client.start.assert_called_once()
```

- [ ] **Step 2: 테스트 실행 (실패 확인)**

```bash
pytest tests/test_core_auth.py -v
```

Expected: FAIL

- [ ] **Step 3: `core/auth.py` 구현**

```python
# src/invagent/core/auth.py
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
        sys.exit(1)
```

- [ ] **Step 4: 테스트 실행 (통과 확인)**

```bash
pytest tests/test_core_auth.py -v
```

Expected: PASSED

- [ ] **Step 5: 커밋**

```bash
git add src/invagent/core/auth.py tests/test_core_auth.py
git commit -m "feat: add authenticate function for Telegram login"
```

---

### Task 3: `core/client.py` 작성

**Files:**
- Create: `src/invagent/core/client.py`
- Create: `tests/test_core_client.py`

- [ ] **Step 1: 테스트 작성**

```python
# tests/test_core_client.py
import pytest
from unittest.mock import AsyncMock, patch
from invagent.core.config import Config
from invagent.core.client import TelegramClientManager


@pytest.mark.asyncio
async def test_client_manager_returns_same_instance():
    """get_client() 호출 시 동일한 인스턴스 반환"""
    config = Config(
        api_id=123,
        api_hash="test_hash",
        session_path="/tmp/test_session",
        output_dir="/tmp/outputs",
    )
    
    manager = TelegramClientManager()
    
    with patch("invagent.core.client.TelegramClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client
        
        client1 = await manager.get_client(config)
        client2 = await manager.get_client(config)
        
        assert client1 is client2, "같은 manager에서는 동일한 클라이언트 반환"


@pytest.mark.asyncio
async def test_client_manager_connects_only_once():
    """connect()는 1회만 호출"""
    config = Config(
        api_id=123,
        api_hash="test_hash",
        session_path="/tmp/test_session",
        output_dir="/tmp/outputs",
    )
    
    manager = TelegramClientManager()
    
    with patch("invagent.core.client.TelegramClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client
        
        await manager.get_client(config)
        await manager.get_client(config)
        
        # connect()는 1회만 호출
        assert mock_client.connect.call_count == 1
```

- [ ] **Step 2: 테스트 실행 (실패 확인)**

```bash
pytest tests/test_core_client.py -v
```

Expected: FAIL

- [ ] **Step 3: `core/client.py` 구현**

```python
# src/invagent/core/client.py
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
```

- [ ] **Step 4: 테스트 실행 (통과 확인)**

```bash
pytest tests/test_core_client.py -v
```

Expected: PASSED

- [ ] **Step 5: 커밋**

```bash
git add src/invagent/core/client.py tests/test_core_client.py
git commit -m "feat: add TelegramClientManager singleton"
```

---

## Phase 2: Telegram 연동 모듈

### Task 4: `telegram/link_extractor.py` 작성

**Files:**
- Create: `src/invagent/telegram/link_extractor.py`
- Create: `tests/test_telegram_link_extractor.py`

- [ ] **Step 1: 테스트 작성**

```python
# tests/test_telegram_link_extractor.py
import pytest
from unittest.mock import patch, AsyncMock
from invagent.telegram.link_extractor import LinkExtractor


def test_extract_urls_basic():
    """기본 URL 추출"""
    extractor = LinkExtractor()
    text = "이 링크를 보세요: https://example.com 그리고 http://google.com"
    
    urls = extractor.extract_urls(text)
    
    assert "https://example.com" in urls
    assert "http://google.com" in urls
    assert len(urls) == 2


def test_extract_urls_with_korean_pattern():
    """'링크: ' 패턴으로 시작하는 URL 추출"""
    extractor = LinkExtractor()
    text = "링크: https://example.com"
    
    urls = extractor.extract_urls(text)
    
    assert "https://example.com" in urls


def test_extract_urls_no_duplicates():
    """중복 제거"""
    extractor = LinkExtractor()
    text = "https://example.com https://example.com"
    
    urls = extractor.extract_urls(text)
    
    assert len(urls) == 1
    assert urls[0] == "https://example.com"


def test_extract_urls_empty_text():
    """빈 텍스트 처리"""
    extractor = LinkExtractor()
    
    urls = extractor.extract_urls("")
    
    assert urls == []


@pytest.mark.asyncio
async def test_fetch_content_success():
    """URL 내용 정상 추출"""
    extractor = LinkExtractor()
    
    with patch("invagent.telegram.link_extractor.requests.get") as mock_get:
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = "<html><head><title>Test Page</title></head><body><p>Content here</p></body></html>"
        mock_response.encoding = 'utf-8'
        mock_get.return_value = mock_response
        
        content = await extractor.fetch_content("https://example.com")
        
        assert "Test Page" in content
        assert content != ""


@pytest.mark.asyncio
async def test_fetch_content_timeout():
    """URL 타임아웃 처리"""
    extractor = LinkExtractor()
    
    with patch("invagent.telegram.link_extractor.requests.get") as mock_get:
        import requests
        mock_get.side_effect = requests.exceptions.Timeout()
        
        content = await extractor.fetch_content("https://example.com")
        
        assert "타임아웃" in content or "timeout" in content.lower()
```

- [ ] **Step 2: 테스트 실행 (실패 확인)**

```bash
pytest tests/test_telegram_link_extractor.py -v
```

Expected: FAIL (모듈 없음)

- [ ] **Step 3: `telegram/link_extractor.py` 구현**

```python
# src/invagent/telegram/link_extractor.py
"""URL 추출 및 내용 fetch"""
import re
import asyncio
from typing import Optional

try:
    import requests
    from bs4 import BeautifulSoup
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# URL 정규표현식
URL_PATTERN = re.compile(
    r'https?://[^\s\)]+|'
    r'www\.[^\s\)]+|'
    r't\.me/[^\s\)]+|'
    r'링크:\s*([^\s\)]+)',
    re.IGNORECASE
)


class LinkExtractor:
    """URL 추출 및 내용 가져오기"""
    
    def extract_urls(self, text: str) -> list[str]:
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
    
    async def fetch_content(self, url: str, timeout: int = 5) -> str:
        """URL 내용을 가져와서 핵심 텍스트 추출"""
        if not HAS_REQUESTS:
            return ""
        
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                             'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            
            # requests는 동기 라이브러리이므로 executor에서 실행
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            )
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
            
            # 기사 본문
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
    
    async def extract_and_fetch(self, text: str) -> dict:
        """URL 추출 후 내용까지 가져오기"""
        urls = self.extract_urls(text)
        result = {}
        
        for url in urls:
            content = await self.fetch_content(url)
            result[url] = content
        
        return result
```

- [ ] **Step 4: `telegram/__init__.py` 작성**

```python
# src/invagent/telegram/__init__.py
from .link_extractor import LinkExtractor
from .fetch import MessageFetcher
from .downloader import PDFDownloader

__all__ = ["LinkExtractor", "MessageFetcher", "PDFDownloader"]
```

- [ ] **Step 5: 테스트 실행 (통과 확인)**

```bash
pytest tests/test_telegram_link_extractor.py -v
```

Expected: PASSED

- [ ] **Step 6: 커밋**

```bash
git add src/invagent/telegram/__init__.py src/invagent/telegram/link_extractor.py tests/test_telegram_link_extractor.py
git commit -m "feat: add LinkExtractor for URL extraction and content fetching"
```

---

### Task 5: `telegram/fetch.py` 작성

**Files:**
- Create: `src/invagent/telegram/fetch.py`
- Create: `tests/test_telegram_fetch.py`

- [ ] **Step 1: 테스트 작성**

```python
# tests/test_telegram_fetch.py
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from invagent.core.config import Config
from invagent.core.client import TelegramClientManager
from invagent.telegram.fetch import MessageFetcher


@pytest.mark.asyncio
async def test_message_fetcher_fetch_saved_messages():
    """저장된 메시지 조회"""
    config = Config(
        api_id=123,
        api_hash="test_hash",
        session_path="/tmp/test",
        output_dir="/tmp/outputs",
    )
    
    manager = TelegramClientManager()
    fetcher = MessageFetcher(config, manager)
    
    with patch.object(manager, "get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        
        # Mock 메시지
        mock_msg = AsyncMock()
        mock_msg.id = 1
        mock_msg.date = datetime.now(timezone.utc)
        mock_msg.text = "Test message"
        
        # iter_messages를 비동기 제너레이터로 모킹
        async def async_iter():
            yield mock_msg
        
        mock_client.iter_messages = AsyncMock(return_value=async_iter())
        
        messages = await fetcher.fetch_saved_messages(days=1, fetch_links=False)
        
        assert len(messages) > 0
        assert messages[0]["text"] == "Test message"


def test_message_fetcher_format_messages():
    """메시지 포맷팅"""
    config = Config(
        api_id=123,
        api_hash="test_hash",
        session_path="/tmp/test",
        output_dir="/tmp/outputs",
    )
    
    manager = TelegramClientManager()
    fetcher = MessageFetcher(config, manager)
    
    messages = [
        {
            "id": 1,
            "date": "2026-04-01 10:00",
            "text": "첫 번째 메시지",
            "links_content": ""
        },
        {
            "id": 2,
            "date": "2026-04-01 11:00",
            "text": "두 번째 메시지",
            "links_content": ""
        }
    ]
    
    formatted = fetcher.format_messages(messages)
    
    assert "2026-04-01" in formatted
    assert "첫 번째 메시지" in formatted
    assert "두 번째 메시지" in formatted
```

- [ ] **Step 2: 테스트 실행 (실패 확인)**

```bash
pytest tests/test_telegram_fetch.py -v
```

Expected: FAIL

- [ ] **Step 3: `telegram/fetch.py` 구현**

```python
# src/invagent/telegram/fetch.py
"""저장된 메시지 조회"""
from datetime import datetime, timedelta, timezone
from invagent.core.config import Config
from invagent.core.client import TelegramClientManager
from .link_extractor import LinkExtractor


class MessageFetcher:
    """Telegram 저장된 메시지 조회"""
    
    def __init__(self, config: Config, client_manager: TelegramClientManager):
        self.config = config
        self.client_manager = client_manager
        self.link_extractor = LinkExtractor()
    
    async def fetch_saved_messages(self, days: int, fetch_links: bool = False) -> list[dict]:
        """저장된 메시지 조회
        
        Args:
            days: 최근 N일치 메시지
            fetch_links: 링크 내용 추출 여부
        
        Returns:
            메시지 리스트: [{"id": int, "date": str, "text": str, "links_content": str}, ...]
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        messages = []
        
        client = await self.client_manager.get_client(self.config)
        
        async for msg in client.iter_messages("me", limit=200):
            if msg.date < cutoff:
                break
            if not msg.text:
                continue
            
            text = msg.text.strip()
            links_content = ""
            
            # 링크 추출 및 내용 가져오기
            if fetch_links:
                urls = self.link_extractor.extract_urls(text)
                if urls:
                    links_content = "\n\n"
                    links_content += "━━━ 🔗 링크 내용 ━━━\n"
                    for url in urls:
                        links_content += f"\n📌 {url}\n"
                        content = await self.link_extractor.fetch_content(url)
                        if content:
                            links_content += f"{content}\n"
                    links_content += "━━━━━━━━━━━━━━━━\n"
            
            messages.append({
                "id": msg.id,
                "date": msg.date.astimezone().strftime("%Y-%m-%d %H:%M"),
                "text": text,
                "links_content": links_content,
            })
        
        return messages
    
    def format_messages(self, messages: list[dict]) -> str:
        """메시지를 문자열로 포맷"""
        if not messages:
            return "해당 기간 내 저장된 텍스트 메시지가 없습니다."
        
        output = f"=== 저장한 메시지 ({len(messages)}건) ===\n\n"
        
        for m in reversed(messages):  # 오래된 순으로 출력
            output += f"[{m['date']}]\n"
            output += m["text"] + "\n"
            if m.get("links_content"):
                output += m["links_content"]
            output += "---\n"
        
        return output
```

- [ ] **Step 4: 테스트 실행 (통과 확인)**

```bash
pytest tests/test_telegram_fetch.py -v
```

Expected: PASSED

- [ ] **Step 5: 커밋**

```bash
git add src/invagent/telegram/fetch.py tests/test_telegram_fetch.py
git commit -m "feat: add MessageFetcher for retrieving saved messages"
```

---

### Task 6: `telegram/downloader.py` 작성

**Files:**
- Create: `src/invagent/telegram/downloader.py`
- Create: `tests/test_telegram_downloader.py`

- [ ] **Step 1: 테스트 작성**

```python
# tests/test_telegram_downloader.py
import pytest
from pathlib import Path
from datetime import datetime
from unittest.mock import AsyncMock, patch, MagicMock
from invagent.core.config import Config
from invagent.core.client import TelegramClientManager
from invagent.parsers.pdf_namer import PDFNamer
from invagent.telegram.downloader import PDFDownloader


@pytest.mark.asyncio
async def test_pdf_downloader_download_pdfs(tmp_path):
    """PDF 다운로드 기본 기능"""
    config = Config(
        api_id=123,
        api_hash="test_hash",
        session_path="/tmp/test",
        output_dir=tmp_path / "outputs",
    )
    
    manager = TelegramClientManager()
    pdf_namer = PDFNamer()
    downloader = PDFDownloader(config, manager, pdf_namer)
    
    with patch.object(manager, "get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        
        # Mock entity
        mock_entity = MagicMock()
        mock_client.get_entity = AsyncMock(return_value=mock_entity)
        
        # Mock message with PDF
        from telethon.tl.types import MessageMediaDocument
        
        mock_msg = AsyncMock()
        mock_msg.id = 1
        mock_msg.date = datetime.now()
        mock_msg.media = AsyncMock(spec=MessageMediaDocument)
        mock_msg.media.document = MagicMock()
        mock_msg.media.document.mime_type = "application/pdf"
        mock_msg.media.document.attributes = [MagicMock(file_name="test.pdf")]
        
        async def async_iter():
            yield mock_msg
        
        mock_client.iter_messages = AsyncMock(return_value=async_iter())
        mock_client.download_media = AsyncMock()
        
        results = await downloader.download_pdfs(["test_channel"], days=1)
        
        assert "test_channel" in results
        assert len(results["test_channel"]) > 0


def test_pdf_downloader_get_output_path(tmp_path):
    """출력 경로 생성"""
    config = Config(
        api_id=123,
        api_hash="test_hash",
        session_path="/tmp/test",
        output_dir=tmp_path / "outputs",
    )
    
    manager = TelegramClientManager()
    pdf_namer = PDFNamer()
    downloader = PDFDownloader(config, manager, pdf_namer)
    
    date = datetime(2026, 4, 2)
    path = downloader._get_output_path("test_channel", "report.pdf", date)
    
    # 기본: outputs/reports/{date}/{filename}
    assert str(path).endswith(".pdf")
    assert "2026-04-02" in str(path)
```

- [ ] **Step 2: 테스트 실행 (실패 확인)**

```bash
pytest tests/test_telegram_downloader.py -v
```

Expected: FAIL

- [ ] **Step 3: `telegram/downloader.py` 구현**

```python
# src/invagent/telegram/downloader.py
"""PDF 다운로드"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from telethon.tl.types import MessageMediaDocument
from invagent.core.config import Config
from invagent.core.client import TelegramClientManager
from invagent.parsers.pdf_namer import PDFNamer


class PDFDownloader:
    """Telegram 채널에서 PDF 다운로드"""
    
    def __init__(self, config: Config, client_manager: TelegramClientManager, pdf_namer: PDFNamer):
        self.config = config
        self.client_manager = client_manager
        self.pdf_namer = pdf_namer
    
    async def download_pdfs(self, channels: list[str], days: int) -> dict:
        """지정된 채널에서 PDF 다운로드
        
        Args:
            channels: 채널 이름 리스트
            days: 최근 N일치 메시지
        
        Returns:
            {채널명: [{"filename": str, "path": str, "date": str}, ...], ...}
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        results = {}
        
        client = await self.client_manager.get_client(self.config)
        
        for channel_name in channels:
            print(f"\n📡 [{channel_name}] 검색 중...")
            channel_results = []
            
            try:
                entity = await client.get_entity(channel_name)
                print(f"   ✓ 채널 발견")
            except ValueError as e:
                print(f"   ✗ 채널을 찾을 수 없습니다: {e}")
                continue
            except Exception as e:
                print(f"   ✗ 오류: {e}")
                continue
            
            try:
                msg_count = 0
                pdf_count = 0
                
                async for msg in client.iter_messages(entity, limit=500):
                    msg_count += 1
                    
                    if msg.date < cutoff:
                        print(f"   → {msg_count}개 메시지 검색 (cutoff 도달, {pdf_count}개 PDF 발견)")
                        break
                    
                    if not isinstance(msg.media, MessageMediaDocument):
                        continue
                    
                    doc = msg.media.document
                    if doc.mime_type != "application/pdf":
                        continue
                    
                    pdf_count += 1
                    filename = doc.attributes[0].file_name if doc.attributes else f"document_{msg.id}.pdf"
                    date_str = msg.date.astimezone().strftime("%Y-%m-%d")
                    
                    out_path = self._get_output_path(channel_name, filename, msg.date)
                    
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
    
    def _get_output_path(self, channel: str, filename: str, date: datetime) -> Path:
        """출력 경로 생성 (pdf_namer 사용)"""
        date_str = date.astimezone().strftime("%Y-%m-%d")
        
        # pdf_namer에서 파일명 생성 (현재는 기본값)
        new_filename = self.pdf_namer.generate_filename(channel, filename, date)
        
        out_dir = self.config.output_dir / "reports" / date_str
        out_dir.mkdir(parents=True, exist_ok=True)
        
        return out_dir / new_filename
```

- [ ] **Step 4: 테스트 실행 (통과 확인)**

```bash
pytest tests/test_telegram_downloader.py -v
```

Expected: PASSED

- [ ] **Step 5: 커밋**

```bash
git add src/invagent/telegram/downloader.py tests/test_telegram_downloader.py
git commit -m "feat: add PDFDownloader with consistent filename handling"
```

---

## Phase 3: 파일명 규칙 모듈

### Task 7: `parsers/pdf_namer.py` 작성

**Files:**
- Create: `src/invagent/parsers/pdf_namer.py`
- Create: `tests/test_parsers_pdf_namer.py`

- [ ] **Step 1: 테스트 작성**

```python
# tests/test_parsers_pdf_namer.py
import pytest
from datetime import datetime
from invagent.parsers.pdf_namer import PDFNamer, Rule


def test_pdf_namer_default_rule():
    """규칙이 없는 채널은 기본 패턴 사용"""
    namer = PDFNamer()
    
    date = datetime(2026, 4, 2)
    filename = namer.generate_filename("unknown_channel", "report.pdf", date)
    
    assert "unknown_channel" in filename
    assert "report" in filename
    assert "2026-04-02" in filename or "20260402" in filename
    assert filename.endswith(".pdf")


def test_pdf_namer_custom_rule():
    """커스텀 규칙 추가"""
    class TestRule(Rule):
        def parse_and_generate(self, filename: str, date: datetime) -> str:
            return f"test_{date.strftime('%Y%m%d')}_{filename}"
    
    namer = PDFNamer()
    namer.add_rule("test_channel", TestRule())
    
    date = datetime(2026, 4, 2)
    filename = namer.generate_filename("test_channel", "report.pdf", date)
    
    assert filename == "test_20260402_report.pdf"


def test_pdf_namer_duplicate_removal():
    """중복 URL 제거"""
    namer = PDFNamer()
    
    date = datetime(2026, 4, 2)
    filename1 = namer.generate_filename("channel", "report.pdf", date)
    filename2 = namer.generate_filename("channel", "report.pdf", date)
    
    # 같은 입력 → 같은 출력
    assert filename1 == filename2
```

- [ ] **Step 2: 테스트 실행 (실패 확인)**

```bash
pytest tests/test_parsers_pdf_namer.py -v
```

Expected: FAIL

- [ ] **Step 3: `parsers/pdf_namer.py` 구현**

```python
# src/invagent/parsers/pdf_namer.py
"""PDF 파일명 규칙 관리"""
from datetime import datetime
from abc import ABC, abstractmethod


class Rule(ABC):
    """채널별 파일명 규칙 base class"""
    
    @abstractmethod
    def parse_and_generate(self, filename: str, date: datetime) -> str:
        """원본 파일명 → 통일된 파일명
        
        Args:
            filename: 원본 파일명
            date: 메시지 날짜
        
        Returns:
            변환된 파일명
        """
        raise NotImplementedError


class DefaultRule(Rule):
    """기본 규칙 (규칙 없는 채널)"""
    
    def parse_and_generate(self, filename: str, date: datetime) -> str:
        date_str = date.strftime("%Y-%m-%d")
        # 확장자 분리
        if '.' in filename:
            name, ext = filename.rsplit('.', 1)
            return f"{name}_{date_str}.{ext}"
        else:
            return f"{filename}_{date_str}.pdf"


class PDFNamer:
    """파일명 규칙 관리"""
    
    def __init__(self):
        self.rules = {}
    
    def add_rule(self, channel: str, rule: Rule):
        """채널별 규칙 추가"""
        self.rules[channel] = rule
    
    def generate_filename(self, channel: str, original_filename: str, date: datetime) -> str:
        """규칙에 맞게 파일명 생성
        
        Args:
            channel: 채널 이름
            original_filename: 원본 파일명
            date: 메시지 날짜
        
        Returns:
            변환된 파일명
        """
        if channel in self.rules:
            return self.rules[channel].parse_and_generate(original_filename, date)
        else:
            # 규칙이 없으면 기본 규칙 적용
            default_rule = DefaultRule()
            return default_rule.parse_and_generate(original_filename, date)
```

- [ ] **Step 4: `parsers/__init__.py` 작성**

```python
# src/invagent/parsers/__init__.py
from .pdf_namer import PDFNamer, Rule

__all__ = ["PDFNamer", "Rule"]
```

- [ ] **Step 5: 테스트 실행 (통과 확인)**

```bash
pytest tests/test_parsers_pdf_namer.py -v
```

Expected: PASSED

- [ ] **Step 6: 커밋**

```bash
git add src/invagent/parsers/__init__.py src/invagent/parsers/pdf_namer.py tests/test_parsers_pdf_namer.py
git commit -m "feat: add PDFNamer with pluggable rule system for filename standardization"
```

---

## Phase 4: 주가 추적 모듈

### Task 8: `tracking/stock_tracker.py` 작성

**Files:**
- Create: `src/invagent/tracking/stock_tracker.py`
- Create: `tests/test_tracking_stock_tracker.py`

- [ ] **Step 1: 테스트 작성**

```python
# tests/test_tracking_stock_tracker.py
import pytest
from pathlib import Path
from datetime import datetime
from invagent.tracking.stock_tracker import StockTracker


def test_stock_tracker_add_stock(tmp_path):
    """종목 추가"""
    data_file = tmp_path / "stocks.json"
    tracker = StockTracker(data_file=data_file)
    
    tracker.add_stock("AAPL", "Apple", "기술")
    
    assert "AAPL" in tracker.stocks
    assert tracker.stocks["AAPL"]["name"] == "Apple"
    assert tracker.stocks["AAPL"]["sector"] == "기술"


def test_stock_tracker_set_target_price(tmp_path):
    """목표 주가 설정"""
    data_file = tmp_path / "stocks.json"
    tracker = StockTracker(data_file=data_file)
    
    tracker.add_stock("AAPL", "Apple", "기술")
    tracker.set_target_price("AAPL", 150.0, datetime(2026, 4, 1))
    tracker.set_target_price("AAPL", 155.0, datetime(2026, 4, 2))
    
    history = tracker.get_target_history("AAPL")
    
    assert len(history) == 2
    assert history[0]["price"] == 150.0
    assert history[1]["price"] == 155.0


def test_stock_tracker_get_target_history(tmp_path):
    """목표 주가 이력 조회"""
    data_file = tmp_path / "stocks.json"
    tracker = StockTracker(data_file=data_file)
    
    tracker.add_stock("MSFT", "Microsoft", "기술")
    tracker.set_target_price("MSFT", 300.0, datetime(2026, 3, 1))
    tracker.set_target_price("MSFT", 310.0, datetime(2026, 3, 15))
    
    history = tracker.get_target_history("MSFT")
    
    assert len(history) == 2
    assert all("date" in h and "price" in h for h in history)


def test_stock_tracker_save_and_load(tmp_path):
    """저장 및 로드"""
    data_file = tmp_path / "stocks.json"
    
    # 저장
    tracker1 = StockTracker(data_file=data_file)
    tracker1.add_stock("AAPL", "Apple", "기술")
    tracker1.set_target_price("AAPL", 150.0, datetime(2026, 4, 1))
    tracker1.save()
    
    # 로드
    tracker2 = StockTracker(data_file=data_file)
    
    assert "AAPL" in tracker2.stocks
    assert tracker2.stocks["AAPL"]["name"] == "Apple"
    assert len(tracker2.get_target_history("AAPL")) == 1


def test_stock_tracker_stock_not_found(tmp_path):
    """존재하지 않는 종목 처리"""
    data_file = tmp_path / "stocks.json"
    tracker = StockTracker(data_file=data_file)
    
    # 존재하지 않는 종목에 목표주가 설정 시도
    with pytest.raises(ValueError):
        tracker.set_target_price("UNKNOWN", 100.0, datetime(2026, 4, 1))
```

- [ ] **Step 2: 테스트 실행 (실패 확인)**

```bash
pytest tests/test_tracking_stock_tracker.py -v
```

Expected: FAIL

- [ ] **Step 3: `tracking/stock_tracker.py` 구현**

```python
# src/invagent/tracking/stock_tracker.py
"""종목/주가 추적"""
import json
from datetime import datetime
from pathlib import Path
from typing import Optional


class StockTracker:
    """종목별 목표 주가, 투자 의견 관리"""
    
    def __init__(self, data_file: Path = Path("context/stocks.json")):
        self.data_file = Path(data_file)
        self.stocks = self._load_stocks()
    
    def add_stock(self, ticker: str, name: str, sector: str):
        """종목 추가
        
        Args:
            ticker: 종목 티커 (예: AAPL)
            name: 종목명 (예: Apple)
            sector: 섹터 (예: 기술)
        """
        if ticker not in self.stocks:
            self.stocks[ticker] = {
                "name": name,
                "sector": sector,
                "target_prices": []
            }
    
    def set_target_price(self, ticker: str, price: float, date: datetime):
        """목표 주가 설정
        
        Args:
            ticker: 종목 티커
            price: 목표 주가
            date: 설정 날짜
        
        Raises:
            ValueError: 종목이 없을 때
        """
        if ticker not in self.stocks:
            raise ValueError(f"종목을 찾을 수 없습니다: {ticker}")
        
        self.stocks[ticker]["target_prices"].append({
            "date": date.strftime("%Y-%m-%d"),
            "price": price
        })
    
    def get_target_history(self, ticker: str) -> list[dict]:
        """목표 주가 변경 이력 조회
        
        Args:
            ticker: 종목 티커
        
        Returns:
            목표주가 이력 리스트
        """
        if ticker not in self.stocks:
            return []
        
        return self.stocks[ticker]["target_prices"]
    
    def save(self):
        """변경사항 파일에 저장"""
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.data_file, 'w', encoding='utf-8') as f:
            json.dump(self.stocks, f, ensure_ascii=False, indent=2)
    
    def _load_stocks(self) -> dict:
        """저장된 데이터 로드"""
        if self.data_file.exists():
            with open(self.data_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
```

- [ ] **Step 4: `tracking/__init__.py` 작성**

```python
# src/invagent/tracking/__init__.py
from .stock_tracker import StockTracker

__all__ = ["StockTracker"]
```

- [ ] **Step 5: 테스트 실행 (통과 확인)**

```bash
pytest tests/test_tracking_stock_tracker.py -v
```

Expected: PASSED

- [ ] **Step 6: 커밋**

```bash
git add src/invagent/tracking/__init__.py src/invagent/tracking/stock_tracker.py tests/test_tracking_stock_tracker.py
git commit -m "feat: add StockTracker for managing target prices and stock data"
```

---

## Phase 5: CLI 통합

### Task 9: `cli.py` 작성

**Files:**
- Create: `src/invagent/cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: 테스트 작성 (기본 CLI 구조)**

```python
# tests/test_cli.py
import pytest
from click.testing import CliRunner
from invagent.cli import cli


def test_cli_help():
    """CLI 도움말 표시"""
    runner = CliRunner()
    result = runner.invoke(cli, ['--help'])
    
    assert result.exit_code == 0
    assert "invagent" in result.output.lower()


def test_cli_authenticate_help():
    """authenticate 명령어 도움말"""
    runner = CliRunner()
    result = runner.invoke(cli, ['authenticate', '--help'])
    
    assert result.exit_code == 0
    assert "authenticate" in result.output.lower() or "인증" in result.output


def test_cli_fetch_messages_help():
    """fetch-messages 명령어 도움말"""
    runner = CliRunner()
    result = runner.invoke(cli, ['fetch-messages', '--help'])
    
    assert result.exit_code == 0
```

- [ ] **Step 2: 테스트 실행 (실패 확인)**

```bash
pytest tests/test_cli.py::test_cli_help -v
```

Expected: FAIL

- [ ] **Step 3: `cli.py` 구현**

```python
# src/invagent/cli.py
"""invagent CLI 인터페이스"""
import asyncio
import sys
import click
from pathlib import Path

from .core.config import Config
from .core.client import TelegramClientManager
from .core.auth import authenticate
from .telegram.fetch import MessageFetcher
from .telegram.downloader import PDFDownloader
from .parsers.pdf_namer import PDFNamer
from .tracking.stock_tracker import StockTracker

DEFAULT_CHANNELS = [
    "소중한추억.",
    "선진짱 주식공부방",
    "리포트 갤러리",
    "영리한타이거의 주식공부방",
]


@click.group()
def cli():
    """invagent - 투자 의사 결정 보조 도구"""
    pass


@cli.command()
def authenticate_cmd():
    """텔레그램 인증 (최초 1회만)"""
    try:
        config = Config.from_env()
        print("🔐 텔레그램 인증을 시작합니다...")
        asyncio.run(authenticate(config))
    except ValueError as e:
        print(f"❌ 오류: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 인증 실패: {e}")
        sys.exit(1)


@cli.command()
@click.option('--days', type=int, default=1, help='최근 N일치 메시지 (기본: 1)')
@click.option('--fetch-links', is_flag=True, help='링크 내용 자동 추출')
def fetch_messages(days, fetch_links):
    """텔레그램 저장 메시지 조회"""
    try:
        config = Config.from_env()
        client_manager = TelegramClientManager()
        fetcher = MessageFetcher(config, client_manager)
        
        print(f"📬 저장된 메시지 조회 (최근 {days}일)...")
        messages = asyncio.run(fetcher.fetch_saved_messages(days, fetch_links=fetch_links))
        
        formatted = fetcher.format_messages(messages)
        print(formatted)
    
    except ValueError as e:
        print(f"❌ 오류: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 실패: {e}")
        sys.exit(1)


@cli.command()
@click.option('--days', type=int, default=1, help='최근 N일치 메시지 (기본: 1)')
@click.option('--channels', multiple=True, help='검색할 채널 (미지정 시 기본 채널)')
def download_pdfs(days, channels):
    """PDF 다운로드 (파일명 통일)"""
    try:
        config = Config.from_env()
        client_manager = TelegramClientManager()
        pdf_namer = PDFNamer()
        downloader = PDFDownloader(config, client_manager, pdf_namer)
        
        target_channels = channels if channels else DEFAULT_CHANNELS
        
        print("🔧 텔레그램 PDF 다운로더")
        print(f"   기간: 최근 {days}일")
        print(f"   채널: {len(target_channels)}개")
        
        results = asyncio.run(downloader.download_pdfs(target_channels, days))
        
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
    
    except ValueError as e:
        print(f"❌ 오류: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 실패: {e}")
        sys.exit(1)


@cli.command()
@click.argument('ticker')
@click.option('--name', required=True, help='종목명')
@click.option('--sector', required=True, help='섹터')
def add_stock(ticker, name, sector):
    """종목 추가"""
    try:
        tracker = StockTracker()
        tracker.add_stock(ticker, name, sector)
        tracker.save()
        print(f"✓ 종목 추가됨: {ticker} ({name}) - {sector}")
    except Exception as e:
        print(f"❌ 오류: {e}")
        sys.exit(1)


@cli.command()
@click.argument('ticker')
@click.option('--price', type=float, required=True, help='목표 주가')
def set_target(ticker, price):
    """종목 목표 주가 설정"""
    try:
        tracker = StockTracker()
        if ticker not in tracker.stocks:
            print(f"❌ 종목을 찾을 수 없습니다: {ticker}")
            sys.exit(1)
        
        from datetime import datetime
        tracker.set_target_price(ticker, price, datetime.now())
        tracker.save()
        print(f"✓ {ticker} 목표주가 설정: {price}")
    except ValueError as e:
        print(f"❌ 오류: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 실패: {e}")
        sys.exit(1)


@cli.command()
@click.argument('ticker')
def show_stock(ticker):
    """종목 정보 조회"""
    try:
        tracker = StockTracker()
        
        if ticker not in tracker.stocks:
            print(f"❌ 종목을 찾을 수 없습니다: {ticker}")
            sys.exit(1)
        
        stock = tracker.stocks[ticker]
        history = tracker.get_target_history(ticker)
        
        print(f"\n📊 종목 정보: {ticker}")
        print(f"   이름: {stock['name']}")
        print(f"   섹터: {stock['sector']}")
        print(f"\n🎯 목표 주가 이력:")
        
        if history:
            for h in reversed(history):
                print(f"   {h['date']}: {h['price']}")
        else:
            print("   (이력 없음)")
    
    except Exception as e:
        print(f"❌ 오류: {e}")
        sys.exit(1)


if __name__ == '__main__':
    cli()
```

- [ ] **Step 4: 테스트 실행 (통과 확인)**

```bash
pytest tests/test_cli.py -v
```

Expected: PASSED

- [ ] **Step 5: 커밋**

```bash
git add src/invagent/cli.py tests/test_cli.py
git commit -m "feat: add CLI interface with click for all features"
```

---

### Task 10: `__init__.py` 정리 및 테스트

**Files:**
- Modify: `src/invagent/__init__.py`
- Delete: `src/invagent/auth_telegram.py`
- Delete: `src/invagent/fetch_telegram.py`
- Delete: `src/invagent/download_telegram_pdfs.py`

- [ ] **Step 1: 새 `__init__.py` 작성**

```python
# src/invagent/__init__.py
"""invagent - 투자 의사 결정 보조 도구"""

__version__ = "0.2.0"

# Core
from .core import Config, TelegramClientManager, authenticate

# Telegram
from .telegram import MessageFetcher, PDFDownloader, LinkExtractor

# Parsers
from .parsers import PDFNamer, Rule

# Tracking
from .tracking import StockTracker

__all__ = [
    # Version
    "__version__",
    
    # Core
    "Config",
    "TelegramClientManager",
    "authenticate",
    
    # Telegram
    "MessageFetcher",
    "PDFDownloader",
    "LinkExtractor",
    
    # Parsers
    "PDFNamer",
    "Rule",
    
    # Tracking
    "StockTracker",
]
```

- [ ] **Step 2: 기존 파일 삭제**

```bash
rm src/invagent/auth_telegram.py
rm src/invagent/fetch_telegram.py
rm src/invagent/download_telegram_pdfs.py
```

- [ ] **Step 3: 임포트 테스트**

```python
# tests/test_imports.py
def test_imports():
    """모든 공개 API import 가능"""
    from invagent import (
        Config,
        TelegramClientManager,
        authenticate,
        MessageFetcher,
        PDFDownloader,
        LinkExtractor,
        PDFNamer,
        Rule,
        StockTracker,
    )
    
    assert Config is not None
    assert TelegramClientManager is not None
    assert authenticate is not None
    assert MessageFetcher is not None
    assert PDFDownloader is not None
    assert LinkExtractor is not None
    assert PDFNamer is not None
    assert Rule is not None
    assert StockTracker is not None
```

- [ ] **Step 4: 테스트 실행**

```bash
pytest tests/test_imports.py -v
```

Expected: PASSED

- [ ] **Step 5: 모든 테스트 실행**

```bash
pytest tests/ -v --tb=short
```

Expected: ALL PASSED

- [ ] **Step 6: CLI 정상 작동 확인**

```bash
uv run python -m invagent --help
uv run python -m invagent fetch-messages --help
uv run python -m invagent download-pdfs --help
uv run python -m invagent add-stock --help
uv run python -m invagent set-target --help
uv run python -m invagent show-stock --help
```

Expected: 모든 커맨드 헬프 표시

- [ ] **Step 7: 커밋**

```bash
git add src/invagent/__init__.py tests/test_imports.py
git rm src/invagent/auth_telegram.py src/invagent/fetch_telegram.py src/invagent/download_telegram_pdfs.py
git commit -m "refactor: reorganize package structure, remove old files"
```

---

## 최종 검증

- [ ] **Step 1: 전체 테스트 재실행**

```bash
pytest tests/ -v --cov=src/invagent
```

Expected: 모든 테스트 통과, 충분한 커버리지

- [ ] **Step 2: README 업데이트 (선택사항)**

```bash
# README에 새 CLI 커맨드 추가
vi README.md
```

- [ ] **Step 3: 최종 커밋**

```bash
git add -A
git commit -m "chore: complete package modernization"
```

- [ ] **Step 4: 브랜치 병합 (필요 시)**

```bash
# 현재 main 브랜치에서 작업했다면, PR로 리뷰 권장
git log --oneline | head -15
```

---

## 실행 완료 체크리스트

- [ ] 모든 Phase 완료
- [ ] 모든 테스트 통과 (pytest)
- [ ] CLI 모든 커맨드 정상 작동
- [ ] 기존 기능(fetch, download) 호환성 유지
- [ ] 파일명 규칙화 구조 완성
- [ ] 주가 추적 기본 기능 구현
- [ ] 모든 변경사항 커밋

