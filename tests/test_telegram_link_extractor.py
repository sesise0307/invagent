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
