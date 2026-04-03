import pytest
from unittest.mock import patch, AsyncMock, MagicMock
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


@pytest.mark.asyncio
async def test_fetch_content_uses_trafilatura():
    """trafilatura로 본문 추출 성공"""
    extractor = LinkExtractor()

    with patch("invagent.telegram.link_extractor.trafilatura.fetch_url") as mock_fetch, \
         patch("invagent.telegram.link_extractor.trafilatura.extract") as mock_extract:
        mock_fetch.return_value = "<html><body><article>본문 내용입니다.</article></body></html>"
        mock_extract.return_value = "본문 내용입니다."

        content = await extractor.fetch_content("https://example.com")

        assert "본문 내용입니다." in content


@pytest.mark.asyncio
async def test_fetch_content_falls_back_to_bs4_when_trafilatura_returns_none():
    """trafilatura가 None 반환 시 BeautifulSoup fallback"""
    extractor = LinkExtractor()

    with patch("invagent.telegram.link_extractor.trafilatura.fetch_url") as mock_fetch, \
         patch("invagent.telegram.link_extractor.trafilatura.extract") as mock_extract, \
         patch("invagent.telegram.link_extractor.requests.get") as mock_get:
        mock_fetch.return_value = "<html></html>"
        mock_extract.return_value = None

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><head><title>Fallback Page</title></head><body><p>fallback content</p></body></html>"
        mock_response.encoding = "utf-8"
        mock_get.return_value = mock_response

        content = await extractor.fetch_content("https://example.com")

        assert content != ""
        assert "[링크 읽기 실패]" not in content


@pytest.mark.asyncio
async def test_fetch_content_long_content_not_truncated_at_300():
    """1500자까지 내용 반환"""
    extractor = LinkExtractor()
    long_text = "가" * 1000

    with patch("invagent.telegram.link_extractor.trafilatura.fetch_url") as mock_fetch, \
         patch("invagent.telegram.link_extractor.trafilatura.extract") as mock_extract:
        mock_fetch.return_value = "<html></html>"
        mock_extract.return_value = long_text

        content = await extractor.fetch_content("https://example.com")

        assert len(content) >= 500  # 300자 제한이 없어졌는지 확인


@pytest.mark.asyncio
async def test_extract_and_fetch_parallel():
    """여러 URL을 병렬로 fetch"""
    extractor = LinkExtractor()
    text = "https://example.com https://google.com"

    call_times = []

    async def fake_fetch(url: str) -> str:
        import asyncio
        call_times.append(asyncio.get_event_loop().time())
        await asyncio.sleep(0.05)
        return f"content of {url}"

    with patch.object(extractor, "fetch_content", side_effect=fake_fetch):
        result = await extractor.extract_and_fetch(text)

    assert len(result["urls"]) == 2
    assert len(result["contents"]) == 2
    # 두 fetch가 거의 동시에 시작됐는지 확인 (0.1초 이내 차이)
    if len(call_times) == 2:
        assert abs(call_times[1] - call_times[0]) < 0.1
