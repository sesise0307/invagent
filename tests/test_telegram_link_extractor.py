import socket

import pytest
from unittest.mock import patch, MagicMock
from invagent.telegram.link_extractor import (
    BlockedURLError,
    LinkExtractor,
    PdfDocument,
    assert_public_url,
    normalize_url,
)


def fake_response(status_code=200, body="", location=None, content_type=None):
    """`_fetch_sync`가 기대하는 스트리밍 응답을 흉내낸다. body는 str 또는 bytes."""
    response = MagicMock()
    response.status_code = status_code
    response.headers = {"Location": location} if location else {}
    if content_type:
        response.headers["Content-Type"] = content_type
    raw = body if isinstance(body, bytes) else body.encode("utf-8")
    response.iter_content.return_value = iter([raw])
    return response


PDF_BYTES = b"%PDF-1.7\r\n%\xe2\xe3\xcf\xd3\r\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"


@pytest.fixture
def public_dns():
    """모든 호스트명이 공개 IP로 해석되게 만든다 (테스트에서 실제 DNS 금지)."""
    resolved = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
    with patch("invagent.telegram.link_extractor.socket.getaddrinfo", return_value=resolved):
        yield


def private_dns(ip):
    """호스트명이 주어진 사설/내부 IP로 해석되게 만드는 패치 컨텍스트."""
    resolved = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]
    return patch("invagent.telegram.link_extractor.socket.getaddrinfo", return_value=resolved)


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


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2Q26 NDR takeaway https://vo.la/tVHYbyy.", "https://vo.la/tVHYbyy"),
        ("자료(https://example.com/a), 참고", "https://example.com/a"),
        ("링크 https://example.com/b, 다음", "https://example.com/b"),
        ("확인! https://example.com/c?x=1!", "https://example.com/c?x=1"),
    ],
)
def test_extract_urls_drops_trailing_sentence_punctuation(text, expected):
    """문장 끝 구두점은 URL에 붙이지 않는다 (2026-10-02 `vo.la/tVHYbyy.` → HTTP 404)"""
    assert LinkExtractor().extract_urls(text) == [expected]


def test_extract_urls_empty_text():
    """빈 텍스트 처리"""
    extractor = LinkExtractor()

    urls = extractor.extract_urls("")

    assert urls == []


@pytest.mark.asyncio
async def test_fetch_content_success(public_dns):
    """URL 내용 정상 추출"""
    extractor = LinkExtractor()

    with patch("invagent.telegram.link_extractor.trafilatura.fetch_url", return_value=None), \
         patch("invagent.telegram.link_extractor.requests.get") as mock_get:
        mock_get.return_value = fake_response(
            body="<html><head><title>Test Page</title></head><body><p>Content here</p></body></html>"
        )

        content = await extractor.fetch_content("https://example.com")

        assert "Test Page" in content
        assert content != ""


@pytest.mark.asyncio
async def test_fetch_content_timeout(public_dns):
    """URL 타임아웃 처리"""
    extractor = LinkExtractor()

    with patch("invagent.telegram.link_extractor.trafilatura.fetch_url", return_value=None), \
         patch("invagent.telegram.link_extractor.requests.get") as mock_get:
        import requests
        mock_get.side_effect = requests.exceptions.Timeout()

        content = await extractor.fetch_content("https://example.com")

        assert "타임아웃" in content or "timeout" in content.lower()


@pytest.mark.asyncio
async def test_fetch_content_uses_trafilatura(public_dns):
    """trafilatura로 본문 추출 성공"""
    extractor = LinkExtractor()

    with patch("invagent.telegram.link_extractor.trafilatura.fetch_url") as mock_fetch, \
         patch("invagent.telegram.link_extractor.trafilatura.extract") as mock_extract:
        mock_fetch.return_value = "<html><body><article>본문 내용입니다.</article></body></html>"
        mock_extract.return_value = "본문 내용입니다."

        content = await extractor.fetch_content("https://example.com")

        assert "본문 내용입니다." in content


@pytest.mark.asyncio
async def test_fetch_content_falls_back_to_bs4_when_trafilatura_returns_none(public_dns):
    """trafilatura가 None 반환 시 BeautifulSoup fallback"""
    extractor = LinkExtractor()

    with patch("invagent.telegram.link_extractor.trafilatura.fetch_url") as mock_fetch, \
         patch("invagent.telegram.link_extractor.trafilatura.extract") as mock_extract, \
         patch("invagent.telegram.link_extractor.requests.get") as mock_get:
        mock_fetch.return_value = "<html></html>"
        mock_extract.return_value = None

        mock_get.return_value = fake_response(
            body="<html><head><title>Fallback Page</title></head><body><p>fallback content</p></body></html>"
        )

        content = await extractor.fetch_content("https://example.com")

        assert content != ""
        assert "[링크 읽기 실패]" not in content


@pytest.mark.asyncio
async def test_fetch_content_long_content_not_truncated_at_300(public_dns):
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


@pytest.mark.asyncio
async def test_extract_and_fetch_hard_timeout():
    """멈춘 URL 1건이 수집 전체를 막지 않고 타임아웃으로 회수된다"""
    import asyncio

    extractor = LinkExtractor(timeout=1, hard_timeout=1)
    text = "https://hang.example.com https://ok.example.com"

    async def fake_fetch(url: str) -> str:
        if "hang" in url:
            await asyncio.sleep(30)
        return f"content of {url}"

    with patch.object(extractor, "fetch_content", side_effect=fake_fetch):
        result = await asyncio.wait_for(extractor.extract_and_fetch(text), timeout=10)

    assert result["contents"]["https://hang.example.com"] == "[링크 읽기 타임아웃]"
    assert result["contents"]["https://ok.example.com"] == "content of https://ok.example.com"


def test_trafilatura_config_has_explicit_download_timeout():
    """trafilatura 다운로드 타임아웃이 명시적으로 주입된다"""
    extractor = LinkExtractor(timeout=7)

    assert extractor._trafilatura_config["DEFAULT"]["DOWNLOAD_TIMEOUT"] == "7"
    assert extractor.hard_timeout == 63


def test_trafilatura_config_disables_redirects():
    """trafilatura 리다이렉트는 목적지 재검사가 불가능하므로 막는다"""
    extractor = LinkExtractor()

    assert extractor._trafilatura_config["DEFAULT"]["MAX_REDIRECTS"] == "0"


def test_normalize_url_adds_https_to_schemeless_host():
    """`www.` 형태는 스킴이 없으므로 https를 붙인다"""
    assert normalize_url("www.example.com/a") == "https://www.example.com/a"
    assert normalize_url("https://example.com") == "https://example.com"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "gopher://example.com/",
        "https://",
    ],
)
def test_assert_public_url_rejects_non_http_schemes_and_hostless(url):
    """http/https가 아니거나 호스트가 없는 URL은 거부된다"""
    with pytest.raises(BlockedURLError):
        assert_public_url(url)


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",       # loopback
        "10.0.0.5",        # 사설
        "192.168.1.1",     # 사설
        "172.16.0.1",      # 사설
        "169.254.169.254", # 클라우드 메타데이터
        "0.0.0.0",         # unspecified
    ],
)
def test_assert_public_url_rejects_internal_addresses(ip):
    """이름이 내부 주소로 해석되면 거부된다 (리터럴 IP·평범한 호스트명 모두)"""
    with private_dns(ip):
        with pytest.raises(BlockedURLError):
            assert_public_url(f"http://{ip}/")
        with pytest.raises(BlockedURLError):
            assert_public_url("http://intranet.example.com/")


def test_assert_public_url_allows_public_address(public_dns):
    """공개 대역으로 해석되는 주소는 통과한다"""
    assert_public_url("https://example.com/path")


def test_assert_public_url_rejects_unresolvable_host():
    """이름 해석 실패는 거부로 처리한다"""
    with patch(
        "invagent.telegram.link_extractor.socket.getaddrinfo",
        side_effect=socket.gaierror("no such host"),
    ):
        with pytest.raises(BlockedURLError):
            assert_public_url("https://nonexistent.invalid/")


@pytest.mark.asyncio
async def test_fetch_content_blocks_internal_url_without_requesting():
    """내부 주소는 trafilatura·requests 어느 쪽도 호출하지 않고 차단된다"""
    extractor = LinkExtractor()

    with private_dns("169.254.169.254"), \
         patch("invagent.telegram.link_extractor.trafilatura.fetch_url") as mock_fetch, \
         patch("invagent.telegram.link_extractor.requests.get") as mock_get:
        content = await extractor.fetch_content("http://169.254.169.254/latest/meta-data/")

    assert content.startswith("[차단된 URL")
    mock_fetch.assert_not_called()
    mock_get.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_content_blocks_redirect_into_internal_network():
    """공개 주소에서 내부 주소로 넘기는 리다이렉트도 홉에서 차단된다"""
    extractor = LinkExtractor()
    resolved = {
        "evil.example.com": "93.184.216.34",
        "169.254.169.254": "169.254.169.254",
    }

    def resolve(host, port, **kwargs):
        ip = resolved[host]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    with patch("invagent.telegram.link_extractor.socket.getaddrinfo", side_effect=resolve), \
         patch("invagent.telegram.link_extractor.trafilatura.fetch_url", return_value=None), \
         patch("invagent.telegram.link_extractor.requests.get") as mock_get:
        mock_get.return_value = fake_response(
            status_code=302, location="http://169.254.169.254/latest/meta-data/"
        )

        content = await extractor.fetch_content("https://evil.example.com/redirect")

    assert content.startswith("[차단된 URL")
    assert mock_get.call_count == 1  # 첫 홉만 나가고 두 번째는 막힌다


@pytest.mark.asyncio
async def test_fetch_content_follows_public_redirect(public_dns):
    """공개 주소로 향하는 리다이렉트는 정상적으로 따라간다"""
    extractor = LinkExtractor()

    with patch("invagent.telegram.link_extractor.trafilatura.fetch_url", return_value=None), \
         patch("invagent.telegram.link_extractor.requests.get") as mock_get:
        mock_get.side_effect = [
            fake_response(status_code=301, location="https://example.org/final"),
            fake_response(body="<html><head><title>Final Page</title></head></html>"),
        ]

        content = await extractor.fetch_content("https://example.com/start")

    assert "Final Page" in content
    assert mock_get.call_count == 2


@pytest.mark.asyncio
async def test_fetch_content_stops_after_redirect_limit(public_dns):
    """리다이렉트 루프는 홉 한도에서 끊긴다"""
    extractor = LinkExtractor()

    with patch("invagent.telegram.link_extractor.trafilatura.fetch_url", return_value=None), \
         patch("invagent.telegram.link_extractor.requests.get") as mock_get:
        mock_get.return_value = fake_response(status_code=302, location="https://example.com/loop")

        content = await extractor.fetch_content("https://example.com/loop")

    assert content.startswith("[차단된 URL")
    assert mock_get.call_count == 6  # MAX_REDIRECTS(5) + 1


def test_read_capped_stops_at_max_response_bytes():
    """응답 본문은 상한까지만 읽고 스트림을 닫는다"""
    from invagent.telegram.link_extractor import MAX_RESPONSE_BYTES

    response = MagicMock()
    response.iter_content.return_value = iter([b"a" * 65536] * 1000)

    body = LinkExtractor._read_capped(response)

    assert len(body) == MAX_RESPONSE_BYTES
    response.close.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_content_reads_naver_blog_post_through_postview(public_dns):
    """네이버 블로그 글 주소는 본문이 들어 있는 PostView 주소로 받아 온다"""
    extractor = LinkExtractor()

    with patch("invagent.telegram.link_extractor.trafilatura.fetch_url") as mock_fetch, \
         patch("invagent.telegram.link_extractor.trafilatura.extract") as mock_extract:
        mock_fetch.return_value = "<html></html>"
        mock_extract.return_value = "HD현대중공업 발전엔진 증설 팔로업입니다."

        content = await extractor.fetch_content("https://blog.naver.com/chacha36/224407253026")

    assert mock_fetch.call_args.args[0] == (
        "https://blog.naver.com/PostView.naver?blogId=chacha36&logNo=224407253026"
    )
    assert "발전엔진 증설" in content


@pytest.mark.asyncio
async def test_fetch_content_reads_mobile_naver_blog_post_through_postview(public_dns):
    """모바일 글 주소도 같은 PostView 주소로 받아 온다"""
    extractor = LinkExtractor()

    with patch("invagent.telegram.link_extractor.trafilatura.fetch_url") as mock_fetch, \
         patch("invagent.telegram.link_extractor.trafilatura.extract") as mock_extract:
        mock_fetch.return_value = "<html></html>"
        mock_extract.return_value = "확신이 없으면 쉬어가도 된다."

        await extractor.fetch_content("https://m.blog.naver.com/kimcharger/224407666480")

    assert mock_fetch.call_args.args[0] == (
        "https://blog.naver.com/PostView.naver?blogId=kimcharger&logNo=224407666480"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://blog.naver.com/chacha36",
        "https://n.news.naver.com/article/001/0016302405?sid=101",
    ],
)
@pytest.mark.asyncio
async def test_fetch_content_leaves_non_post_naver_urls_alone(public_dns, url):
    """네이버라도 블로그 글 주소가 아니면 받은 주소 그대로 요청한다"""
    extractor = LinkExtractor()

    with patch("invagent.telegram.link_extractor.trafilatura.fetch_url") as mock_fetch, \
         patch("invagent.telegram.link_extractor.trafilatura.extract") as mock_extract:
        mock_fetch.return_value = "<html></html>"
        mock_extract.return_value = "본문"

        await extractor.fetch_content(url)

    assert mock_fetch.call_args.args[0] == url


@pytest.mark.asyncio
async def test_fetch_content_keeps_up_to_10000_chars_for_every_link(public_dns):
    """본문은 파일로 가므로 네이버 글이든 다른 링크든 10,000자까지 남긴다"""
    extractor = LinkExtractor()
    long_text = "가" * 12000

    with patch("invagent.telegram.link_extractor.trafilatura.fetch_url") as mock_fetch, \
         patch("invagent.telegram.link_extractor.trafilatura.extract") as mock_extract:
        mock_fetch.return_value = "<html></html>"
        mock_extract.return_value = long_text

        naver = await extractor.fetch_content("https://blog.naver.com/chacha36/224407253026")
        other = await extractor.fetch_content("https://example.com/post")

    assert naver == "가" * 10000 + "..."
    assert other == "가" * 10000 + "..."


@pytest.mark.asyncio
async def test_fetch_content_keeps_only_the_naver_post_body(public_dns):
    """네이버 글은 본문 영역만 남기고 블로그 화면의 부속 문구·페이지 데이터는 버린다"""
    extractor = LinkExtractor()
    postview_html = (
        "<html><body>"
        "<div class='layer'>블로그 마켓 판매자의 이력 관리를 위해 블로그 주소 변경이 불가합니다.</div>"
        "<div class='se-main-container'><p>확신이 없으면 쉬어가도 된다.</p>"
        "<p>남의 확신을 빌려서 산 포지션은 가장 먼저 손절하게 된다.</p></div>"
        "<div class='post_footer'>[{\"title\":\"확신이 없으면 쉬어가도 된다\"}]</div>"
        "</body></html>"
    )

    with patch("invagent.telegram.link_extractor.trafilatura.fetch_url", return_value=postview_html):
        content = await extractor.fetch_content("https://blog.naver.com/kimcharger/224407666480")

    assert content == (
        "확신이 없으면 쉬어가도 된다.\n"
        "남의 확신을 빌려서 산 포지션은 가장 먼저 손절하게 된다."
    )


@pytest.mark.asyncio
async def test_fetch_content_joins_naver_paragraph_spans_and_drops_blank_paragraphs(public_dns):
    """문단 안의 강조 조각은 한 줄로 잇고, 보이지 않는 공백만 있는 문단은 버린다"""
    extractor = LinkExtractor()
    postview_html = (
        "<div class='se-main-container'>"
        "<p class='se-text-paragraph'><span>그동안 계속 확인하려 했던 ‘</span>"
        "<b>추가 증설</b><span>’이 실제로 나왔습니다.</span></p>"
        "<p class='se-text-paragraph'><span>\u200b</span></p>"
        "<p class='se-text-paragraph'><span>여기에 </span><span>미국 데이터센터향 수주까지</span></p>"
        "</div>"
    )

    with patch("invagent.telegram.link_extractor.trafilatura.fetch_url", return_value=postview_html):
        content = await extractor.fetch_content("https://blog.naver.com/chacha36/224407253026")

    assert content == (
        "그동안 계속 확인하려 했던 ‘추가 증설’이 실제로 나왔습니다.\n"
        "여기에 미국 데이터센터향 수주까지"
    )


@pytest.mark.asyncio
async def test_fetch_content_returns_pdf_bytes_instead_of_decoded_text(public_dns):
    """PDF 응답은 utf-8로 풀지 않고 원본 바이트 그대로 돌려준다 (2026-10-02 메리츠 PDF가 깨진 글자 18KB로 저장됐다)"""
    extractor = LinkExtractor()

    with patch("invagent.telegram.link_extractor.trafilatura.fetch_url", return_value=None), \
         patch("invagent.telegram.link_extractor.requests.get") as mock_get:
        mock_get.side_effect = [
            fake_response(status_code=302, location="http://home.imeritz.com/research/report.pdf"),
            fake_response(body=PDF_BYTES, content_type="application/pdf"),
        ]

        content = await extractor.fetch_content("https://vo.la/zapQlKL")

    assert content == PdfDocument(PDF_BYTES)


@pytest.mark.asyncio
async def test_fetch_content_reports_pdf_over_size_cap_instead_of_truncating(public_dns, monkeypatch):
    """상한을 넘는 PDF는 잘린 파일(열리지 않는다)을 넘기지 않고 실패 표시를 돌려준다"""
    monkeypatch.setattr("invagent.telegram.link_extractor.MAX_PDF_BYTES", len(PDF_BYTES) - 1)
    extractor = LinkExtractor()

    with patch("invagent.telegram.link_extractor.trafilatura.fetch_url", return_value=None), \
         patch("invagent.telegram.link_extractor.requests.get") as mock_get:
        mock_get.return_value = fake_response(body=PDF_BYTES, content_type="application/pdf")

        content = await extractor.fetch_content("https://example.com/big.pdf")

    assert isinstance(content, str) and content.startswith("[PDF 용량 초과")


@pytest.mark.asyncio
async def test_fetch_content_downloads_direct_pdf_link_once(public_dns):
    """`.pdf` 직링크는 trafilatura를 거치지 않는다 — PDF를 두 번 받으면 링크당 상한 시간을 넘긴다"""
    extractor = LinkExtractor()
    url = "https://www.iprovest.com/upload/research/report/cominf/20261002/20261002_003230.pdf"

    with patch("invagent.telegram.link_extractor.trafilatura.fetch_url") as mock_fetch, \
         patch("invagent.telegram.link_extractor.requests.get") as mock_get:
        mock_get.return_value = fake_response(body=PDF_BYTES, content_type="application/pdf")

        content = await extractor.fetch_content(url)

    assert content == PdfDocument(PDF_BYTES)
    mock_fetch.assert_not_called()
    assert mock_get.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "viewer, document",
    [
        (
            "https://research.koreainvestment.com/streamdocs/view/sd;streamdocsId=eyJ.abc-_9",
            "https://research.koreainvestment.com/streamdocs/v4/documents/eyJ.abc-_9",
        ),
        (
            "https://www.samsungpop.com/streamdocs/mail/sd;streamdocsId=9KRQo3Us-RR_oN",
            "https://www.samsungpop.com/streamdocs/v4/documents/9KRQo3Us-RR_oN/custom",
        ),
    ],
)
async def test_fetch_content_reads_streamdocs_viewer_as_its_pdf(public_dns, viewer, document):
    """증권사 StreamDocs 뷰어(제목만 있는 껍데기)는 리다이렉트 뒤에서도 문서 PDF 주소로 바꿔 받는다"""
    extractor = LinkExtractor()

    with patch("invagent.telegram.link_extractor.trafilatura.fetch_url", return_value=None), \
         patch("invagent.telegram.link_extractor.requests.get") as mock_get:
        mock_get.side_effect = [
            fake_response(status_code=302, location=viewer),
            fake_response(body=PDF_BYTES, content_type="application/octet-stream;charset=utf-8"),
        ]

        content = await extractor.fetch_content("https://vo.la/aFxly62")

    assert content == PdfDocument(PDF_BYTES)
    assert mock_get.call_args_list[1].args[0] == document


KIS_DOWNLOAD_JSP_HTML = (
    '<script>var ugHt9 = {"oLKDP":"662d"};</script>'
    '<script src="/download_pdf.jsp?evfw=sxWx" charset="utf-8"></script><script>\n'
    '\t\tdocument.location = "https://research.koreainvestment.com/streamdocs/openResearch'
    '?dm=:20000&filepath=research/research05&filename=20261002024804700_ko.pdf&option=01";\n'
    "</script>"
)


@pytest.mark.asyncio
async def test_fetch_content_follows_kis_script_redirect_to_report_pdf(public_dns):
    """한투 download_pdf.jsp는 스크립트로 넘긴다 — 따라가서 뷰어 끝의 PDF까지 받는다 (실측 체인 5홉)"""
    extractor = LinkExtractor()
    viewer = "http://research.koreainvestment.com/streamdocs/view/sd;streamdocsId=eyJ.x"

    with patch("invagent.telegram.link_extractor.trafilatura.fetch_url", return_value=None), \
         patch("invagent.telegram.link_extractor.requests.get") as mock_get:
        mock_get.side_effect = [
            fake_response(
                status_code=302,
                location="https://securities.koreainvestment.com/download_pdf.jsp"
                "?file=research/research05/20261002024804700_ko&option=01",
            ),
            fake_response(body=KIS_DOWNLOAD_JSP_HTML, content_type="text/html; charset=euc-kr"),
            fake_response(status_code=302, location=viewer),
            fake_response(
                status_code=302,
                location="https://research.koreainvestment.com/streamdocs/v4/documents/eyJ.x",
            ),
            fake_response(body=PDF_BYTES, content_type="application/octet-stream"),
        ]

        content = await extractor.fetch_content("https://vo.la/bvojEIm")

    assert content == PdfDocument(PDF_BYTES)
    requested = [c.args[0] for c in mock_get.call_args_list]
    assert requested[2].startswith("https://research.koreainvestment.com/streamdocs/openResearch?")
    assert requested[3] == "http://research.koreainvestment.com/streamdocs/v4/documents/eyJ.x"


@pytest.mark.asyncio
async def test_fetch_content_ignores_script_redirect_on_other_hosts(public_dns):
    """스크립트 리다이렉트는 허용한 증권사 호스트에서만 따른다 — 아무 페이지의 JS를 따라가지 않는다"""
    extractor = LinkExtractor()

    with patch("invagent.telegram.link_extractor.trafilatura.fetch_url", return_value=None), \
         patch("invagent.telegram.link_extractor.requests.get") as mock_get:
        mock_get.return_value = fake_response(
            body='<title>Other</title><script>document.location = "https://example.org/next";</script>',
            content_type="text/html",
        )

        content = await extractor.fetch_content("https://example.com/page")

    assert "Other" in content
    assert mock_get.call_count == 1


DART_MAIN_HTML = """<html><head><title>일진전기/단일판매ㆍ공급계약체결/2026.10.02</title></head><body>
<p>잠시만 기다려주세요.</p>
<script>
function init() { viewDoc(original.rcpNo, original.dcmNo, original.eleId, original.offset, original.length, original.dtd, original.tocNo); }
viewDoc("20261002800002", "11600599", "0", "0", "0", "HTML", "");
</script></body></html>"""

DART_VIEWER_HTML = (
    '<html><head><meta content="text/html; charset=euc-kr" http-equiv="Content-Type">'
    "<title></title><style>.xforms td { padding-left:0px; }</style></head><body>"
    "<table><tr><td>계약금액(원)</td><td>187,172,249,990</td></tr>"
    "<tr><td>계약상대</td><td>J. MURPHY &amp; SONS LIMITED</td></tr></table></body></html>"
).encode("cp949")


@pytest.mark.asyncio
async def test_fetch_content_reads_dart_filing_body_behind_frame(public_dns):
    """DART main.do는 틀뿐이다 — viewDoc 인자로 viewer.do 본문을 받아 MS949로 푼다 (2026-10-02 4건이 제목만 남았다)"""
    extractor = LinkExtractor()

    with patch("invagent.telegram.link_extractor.trafilatura.fetch_url") as mock_fetch, \
         patch("invagent.telegram.link_extractor.requests.get") as mock_get:
        mock_get.side_effect = [
            fake_response(body=DART_MAIN_HTML, content_type="text/html; charset=UTF-8"),
            fake_response(body=DART_VIEWER_HTML, content_type="text/html; charset=MS949"),
        ]

        content = await extractor.fetch_content(
            "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261002800002"
        )

    assert mock_get.call_args_list[1].args[0] == (
        "https://dart.fss.or.kr/report/viewer.do?rcpNo=20261002800002&dcmNo=11600599"
        "&eleId=0&offset=0&length=0&dtd=HTML"
    )
    assert "계약금액(원)\n187,172,249,990" in content
    assert "J. MURPHY & SONS LIMITED" in content
    assert "padding-left" not in content
    mock_fetch.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_content_skips_login_walled_pages_without_requesting(public_dns):
    """로그인해야 보이는 페이지(awakeplus 계약 게시판)는 받지 않는다 — 받아도 로그인 안내문뿐이다"""
    extractor = LinkExtractor()

    with patch("invagent.telegram.link_extractor.trafilatura.fetch_url") as mock_fetch, \
         patch("invagent.telegram.link_extractor.requests.get") as mock_get:
        content = await extractor.fetch_content("https://www.awakeplus.co.kr/board/contract/103590")

    assert content.startswith("[건너뜀: 로그인")
    mock_fetch.assert_not_called()
    mock_get.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_content_still_reads_other_awakeplus_pages(public_dns):
    """같은 사이트라도 공개 페이지(공시 상세)는 그대로 받는다"""
    extractor = LinkExtractor()

    with patch("invagent.telegram.link_extractor.trafilatura.fetch_url", return_value="<html/>"), \
         patch("invagent.telegram.link_extractor.trafilatura.extract", return_value="[일진전기] 단일판매"):
        content = await extractor.fetch_content("https://www.awakeplus.co.kr/data/view/20261002800002")

    assert content == "[일진전기] 단일판매"


@pytest.mark.asyncio
async def test_fetch_content_blocks_script_redirect_into_internal_network():
    """허용 호스트의 스크립트 리다이렉트도 목적지는 홉마다 다시 검사한다"""
    extractor = LinkExtractor()
    resolved = {
        "securities.koreainvestment.com": "93.184.216.34",
        "169.254.169.254": "169.254.169.254",
    }

    def resolve(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (resolved[host], port))]

    with patch("invagent.telegram.link_extractor.socket.getaddrinfo", side_effect=resolve), \
         patch("invagent.telegram.link_extractor.trafilatura.fetch_url", return_value=None), \
         patch("invagent.telegram.link_extractor.requests.get") as mock_get:
        mock_get.return_value = fake_response(
            body='<script>document.location = "http://169.254.169.254/latest/meta-data/";</script>',
            content_type="text/html",
        )

        content = await extractor.fetch_content(
            "https://securities.koreainvestment.com/download_pdf.jsp?file=x"
        )

    assert content.startswith("[차단된 URL")
    assert mock_get.call_count == 1


@pytest.mark.asyncio
async def test_fetch_content_reads_dart_filing_with_xsd_document_type(public_dns):
    """대량보유 보고서처럼 문서 형식이 `dart4.xsd`인 공시도 본문 주소를 찾는다"""
    extractor = LinkExtractor()
    frame = 'viewDoc("20261002000146", "11601090", "1", "805", "7650", "dart4.xsd", "");'

    with patch("invagent.telegram.link_extractor.requests.get") as mock_get:
        mock_get.side_effect = [
            fake_response(body=f"<script>{frame}</script>", content_type="text/html; charset=UTF-8"),
            fake_response(body="<table><tr><td>보고후 5.30%</td></tr></table>", content_type="text/html"),
        ]

        content = await extractor.fetch_content(
            "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261002000146"
        )

    assert mock_get.call_args_list[1].args[0].endswith(
        "rcpNo=20261002000146&dcmNo=11601090&eleId=1&offset=805&length=7650&dtd=dart4.xsd"
    )
    assert "보고후 5.30%" in content


def test_default_hard_timeout_leaves_room_for_report_pdfs():
    """링크당 상한은 증권사 리포트 PDF가 끝까지 내려올 만큼 둔다 (한투 12.3MB 단독 35초, 병렬이면 더 길다)"""
    extractor = LinkExtractor()

    assert extractor.timeout == 10
    assert extractor.hard_timeout == 90


@pytest.mark.asyncio
async def test_fetch_content_keeps_dart_tables_even_when_body_has_paragraphs(public_dns):
    """DART 본문은 표가 전부다 — 제목 문단만 집고 표를 버리면 안 된다 (대량보유 보고서가 제목 한 줄로 남았다)"""
    extractor = LinkExtractor()
    frame = 'viewDoc("20261002000146", "11601090", "1", "805", "7650", "dart4.xsd", "");'
    viewer = (
        "<p>주식등의 대량보유상황보고서</p>"
        "<table><tr><td>보고자</td><td>국민연금공단</td></tr>"
        "<tr><td>보고후 보유비율</td><td>5.30</td></tr></table>"
    )

    with patch("invagent.telegram.link_extractor.requests.get") as mock_get:
        mock_get.side_effect = [
            fake_response(body=f"<script>{frame}</script>", content_type="text/html; charset=UTF-8"),
            fake_response(body=viewer.encode("cp949"), content_type="text/html; charset=MS949"),
        ]

        content = await extractor.fetch_content(
            "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261002000146"
        )

    assert "주식등의 대량보유상황보고서" in content
    assert "국민연금공단" in content and "5.30" in content
