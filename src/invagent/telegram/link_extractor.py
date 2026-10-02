"""
URL 추출 및 내용 fetch 기능을 담당하는 LinkExtractor 클래스.

이 모듈은 텍스트에서 URL을 추출하고, URL 내용을 가져올 수 있습니다.
"""

import asyncio
import functools
import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Optional, Union
from urllib.parse import urljoin, urlparse

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

# 문장 끝 구두점은 URL에 들어가지 않는다고 본다. 메시지 본문은 URL 바로 뒤에 마침표·쉼표를
# 붙여 쓰는 경우가 많고, 그대로 요청하면 404가 난다(2026-10-02 `vo.la/tVHYbyy.`).
TRAILING_URL_PUNCTUATION = ".,;:!?'\"’”>]"

TELEGRAM_URL_PATTERN = re.compile(
    r'https?://t\.me/[^\s\)]*|'  # https://t.me/ 링크
    r'(?<!\S)t\.me/[^\s\)]*',    # t.me/ 링크 (단어 경계)
    re.IGNORECASE
)

# fetch 대상 URL은 텔레그램 메시지 본문에서 나온다. 즉 신뢰할 수 없는 입력이므로
# 그대로 요청하면 SSRF다. http/https 외의 스킴과 비공개 대역(루프백, 사설망,
# 링크로컬 169.254.169.254 클라우드 메타데이터 포함)을 차단하고, 응답 크기에도
# 상한을 둔다.
ALLOWED_SCHEMES = ("http", "https")
# 홉은 HTTP 리다이렉트와 스크립트 리다이렉트를 합쳐 센다. 한투 리포트 링크는 단축 URL →
# download_pdf.jsp(스크립트) → openResearch → 뷰어(http) → 뷰어(https)로 5번 요청한다.
MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
# 증권사 리포트 PDF는 수 MB가 흔하다. 잘린 PDF는 열리지 않으므로 텍스트와 따로 넉넉히 잡는다.
MAX_PDF_BYTES = 20 * 1024 * 1024
# 링크 1건의 상한 = 요청 타임아웃 × 이 배수. 멈춘 연결은 요청 타임아웃(읽기 간격)이 먼저 끊으므로
# 이 상한은 느리게라도 계속 내려오는 다운로드에만 걸린다. 리포트 PDF는 크다 — 2026-10-02 한투
# 전기전자 12.3MB가 단독 35초, 메리츠 0.9MB가 16초였고 병렬일 때 30초 상한에서 끊겼다.
HARD_TIMEOUT_MULTIPLIER = 9
PDF_MAGIC = b"%PDF"


@dataclass(frozen=True)
class PdfDocument:
    """링크 본문이 PDF일 때의 결과. 텍스트로 풀면 깨지므로 원본 바이트를 그대로 넘긴다.

    저장은 호출자(`MessageFetcher`)가 `.pdf` 파일로 하고, 읽기는 요약 서브에이전트가
    파일 읽기 툴로 한다 — 분석 리포트 PDF를 읽는 방식과 같아 로컬 PDF 도구가 필요 없다.
    """

    data: bytes


class LinkFailure(str):
    """링크를 읽지 못했다는 표시(`[링크 읽기 타임아웃]` 등). raw에 그대로 적히도록 문자열이지만,
    실패 여부는 타입으로 판정한다 — 「[일진전기] 단일판매…」처럼 대괄호로 시작하는 정상 본문이
    실패로 오인돼 파일로 빠지지 못하고 raw에 남던 문제를 막는다.
    """


LinkContent = Union[str, PdfDocument]


# 네이버 블로그 글 페이지는 본문을 iframe 안의 PostView 문서로 싣는다. 글 주소를 그대로
# 받으면 껍데기의 제목만 남으므로, 글 주소는 본문이 들어 있는 PostView 주소로 바꿔 받는다.
NAVER_BLOG_HOSTS = ("blog.naver.com", "m.blog.naver.com")
# 링크 본문 길이 상한. 본문은 raw가 아니라 링크 원문 파일로 가고 브리핑은 요약만 읽으므로
# 길게 받아도 브리핑 컨텍스트가 늘지 않는다. 글의 결론은 끝에 오는 경우가 많아(2026-09-10
# 블로그 글은 핵심 경고가 1,500자 뒤에 있었다) 넉넉하게 둔다.
MAX_CONTENT_CHARS = 10000
_NAVER_POST_PATH = re.compile(r"^/([A-Za-z0-9_-]+)/(\d+)/?$")


def naver_post_id(url: str) -> Optional[tuple[str, str]]:
    """네이버 블로그 글 주소면 (블로그 ID, 글 번호)를, 아니면 None을 돌려준다."""
    parsed = urlparse(normalize_url(url))
    if parsed.hostname not in NAVER_BLOG_HOSTS:
        return None
    match = _NAVER_POST_PATH.match(parsed.path)
    return match.groups() if match else None


def _naver_postview_url(url: str) -> str:
    """네이버 블로그 글 주소면 PostView 주소를, 아니면 받은 주소를 그대로 돌려준다."""
    post = naver_post_id(url)
    if post is None:
        return url
    blog_id, log_no = post
    return f"https://blog.naver.com/PostView.naver?blogId={blog_id}&logNo={log_no}"


_CHARSET = re.compile(r"charset=([\w.:-]+)", re.IGNORECASE)


def _decode_html(body: bytes, content_type: str) -> str:
    """응답 헤더의 charset으로 푼다. 없거나 모르는 이름이면 utf-8 (DART 본문은 MS949다)."""
    match = _CHARSET.search(content_type)
    if match:
        try:
            return body.decode(match.group(1), errors="replace")
        except LookupError:
            pass
    return body.decode("utf-8", errors="replace")


def _may_be_pdf(content_type: str) -> bool:
    """PDF일 수 있는 응답인가. 문서 뷰어는 PDF를 `application/octet-stream`으로 내려준다."""
    lowered = content_type.lower()
    return "pdf" in lowered or "octet-stream" in lowered


def _skips_trafilatura(url: str) -> bool:
    """trafilatura가 다룰 수 없는 대상인가. PDF를 거기서 한 번 더 받으면 링크당 상한 시간을 넘긴다."""
    return (
        urlparse(url).path.lower().endswith(".pdf")
        or "/streamdocs/" in url
        or _is_dart_frame(url)
    )


# 증권사 리포트 링크는 StreamDocs 뷰어(Angular 앱 껍데기, 제목만 있다)로 끝나는 경우가 많다.
# 뷰어가 내부에서 부르는 문서 API는 PDF를 그대로 내준다. 실측(2026-10-02): 한국투자증권 `view`
# 경로는 `/v4/documents/{id}`, 삼성증권 `mail` 경로는 `/v4/documents/{id}/custom`
# (같은 id로 `/custom` 없이 부르면 HTTP 500).
_STREAMDOCS_VIEWER = re.compile(
    r"^(?P<origin>https?://[^/]+)/streamdocs/(?P<route>view|mail)/sd;streamdocsId=(?P<id>[A-Za-z0-9._~-]+)"
)


def _streamdocs_document_url(url: str) -> Optional[str]:
    """StreamDocs 뷰어 주소면 PDF를 내주는 문서 API 주소를, 아니면 None을 돌려준다."""
    match = _STREAMDOCS_VIEWER.match(url)
    if match is None:
        return None
    document = f"{match['origin']}/streamdocs/v4/documents/{match['id']}"
    return document + "/custom" if match["route"] == "mail" else document


# 스크립트(`document.location = "..."`)로 다음 주소를 넘기는 페이지. 아무 페이지의 JS나 따라가면
# 수집기가 광고·추적 페이지로 끌려가므로 확인된 증권사 호스트만 허용한다.
SCRIPT_REDIRECT_HOSTS = ("securities.koreainvestment.com",)
_SCRIPT_REDIRECT = re.compile(r"""document\.location(?:\.href)?\s*=\s*["']([^"']+)["']""")


# DART 공시 주소(`dsaf001/main.do`)는 목차와 iframe만 있는 틀이고 본문은 `report/viewer.do`에
# 있다. 틀 안의 `viewDoc("접수번호", "문서번호", ...)` 첫 호출 인자가 본문 주소를 정한다.
DART_HOST = "dart.fss.or.kr"
DART_FRAME_PATH = "/dsaf001/main.do"
_DART_VIEW_DOC = re.compile(
    r'viewDoc\("(\d+)",\s*"(\d+)",\s*"(\d+)",\s*"(\d+)",\s*"(\d+)",\s*"([\w.]+)"'
)


def _is_dart_viewer(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.hostname == DART_HOST and parsed.path == "/report/viewer.do"


def _is_dart_frame(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.hostname == DART_HOST and parsed.path == DART_FRAME_PATH


def _next_hop(url: str, body: bytes) -> Optional[str]:
    """본문을 받은 페이지가 진짜 본문을 다른 주소에 두고 있으면 그 주소를 돌려준다."""
    if _is_dart_frame(url):
        match = _DART_VIEW_DOC.search(body.decode("utf-8", errors="replace"))
        if match:
            rcp, dcm, ele, offset, length, dtd = match.groups()
            return (
                f"https://{DART_HOST}/report/viewer.do?rcpNo={rcp}&dcmNo={dcm}"
                f"&eleId={ele}&offset={offset}&length={length}&dtd={dtd}"
            )
    if urlparse(url).hostname in SCRIPT_REDIRECT_HOSTS:
        match = _SCRIPT_REDIRECT.search(body.decode("utf-8", errors="replace"))
        if match:
            return urljoin(url, match.group(1))
    return None


def _rewrite_for_body(url: str) -> str:
    """본문이 다른 주소에 있는 페이지면 그 주소로 바꾼다 (네이버 PostView · StreamDocs 문서)."""
    return _streamdocs_document_url(url) or _naver_postview_url(url)


# 받아도 본문이 없는 페이지: (호스트, 경로 접두사, 사유). 로그인 안내문을 요약 서브에이전트에
# 넘기면 [요약 실패]만 늘어난다. 2026-10-02 awakeplus 계약 게시판 2건 — 계약 내용은 같은
# 포워드 본문에 이미 있다.
SKIPPED_PAGES = (
    ("www.awakeplus.co.kr", "/board/", "로그인 필요"),
)


def _skip_reason(url: str) -> Optional[str]:
    """받지 않을 페이지면 사유를, 아니면 None을 돌려준다."""
    parsed = urlparse(url)
    for host, prefix, reason in SKIPPED_PAGES:
        if parsed.hostname == host and parsed.path.startswith(prefix):
            return reason
    return None


class BlockedURLError(ValueError):
    """공개 인터넷 대상이 아니어서 요청을 거부한 URL."""


def normalize_url(url: str) -> str:
    """스킴이 빠진 `www.example.com` 형태에 https를 붙인다."""
    stripped = url.strip()
    return stripped if "://" in stripped else "https://" + stripped


def assert_public_url(url: str) -> None:
    """공개 인터넷 주소가 아니면 `BlockedURLError`를 던진다.

    호스트명을 직접 해석해 확인하므로 `http://intranet.example.com/`처럼 이름은
    평범하지만 사설 IP를 가리키는 경우도 걸러진다. 요청 시점의 재해석까지
    막지는 못하지만(TOCTOU), 리터럴 내부 주소·내부를 가리키는 이름·내부로
    향하는 리다이렉트라는 실제 공격 경로는 모두 닫는다.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise BlockedURLError(f"허용되지 않는 스킴: {parsed.scheme or '없음'}")

    host = parsed.hostname
    if not host:
        raise BlockedURLError("호스트가 없는 URL")

    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:  # 포트 자리에 숫자가 아닌 값
        raise BlockedURLError("포트가 잘못된 URL") from exc

    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise BlockedURLError(f"이름 해석 실패: {host}") from exc

    for info in infos:
        raw = info[4][0].split("%", 1)[0]  # IPv6 scope id 제거
        try:
            address = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise BlockedURLError(f"주소 해석 실패: {raw}") from exc
        if not address.is_global:
            raise BlockedURLError(f"공개 대역이 아님: {host} -> {address}")


class LinkExtractor:
    """URL 추출 및 내용 fetch 기능을 제공하는 클래스."""

    def __init__(self, timeout: int = 10, hard_timeout: Optional[int] = None) -> None:
        """
        LinkExtractor를 초기화합니다.

        Args:
            timeout: URL 요청의 타임아웃 시간 (초). 기본값: 10초
            hard_timeout: URL 1건 처리의 상한(초). trafilatura·BeautifulSoup 단계까지
                포함해 이 시간을 넘기면 강제로 중단한다. 기본값: timeout의
                `HARD_TIMEOUT_MULTIPLIER`배
        """
        self.timeout = timeout
        self.hard_timeout = (
            hard_timeout if hard_timeout is not None else timeout * HARD_TIMEOUT_MULTIPLIER
        )
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
        # trafilatura의 리다이렉트는 목적지를 재검사할 방법이 없어 SSRF 우회로가 된다.
        # 리다이렉트가 걸린 URL은 홉마다 검사하는 `_fetch_sync` fallback이 처리한다.
        config["DEFAULT"]["MAX_REDIRECTS"] = "0"
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
            url = url.rstrip(TRAILING_URL_PUNCTUATION)
            if url and not self.is_telegram_url(url):
                urls.append(url)

        return list(set(urls))  # 중복 제거

    def remove_telegram_urls(self, text: str) -> str:
        """텍스트에서 텔레그램 링크를 제거합니다."""
        return TELEGRAM_URL_PATTERN.sub("", text).strip()

    async def fetch_content(self, url: str) -> LinkContent:
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
            normalized = normalize_url(url)
            reason = _skip_reason(normalized)
            if reason:
                return LinkFailure(f"[건너뜀: {reason}]")
            target = _rewrite_for_body(normalized)
            assert_public_url(target)
        except BlockedURLError as e:
            return LinkFailure(f"[차단된 URL: {e}]")

        limit = MAX_CONTENT_CHARS

        try:
            # 1차: trafilatura로 본문 추출. HTML 기사가 아닌 대상은 곧장 2차로 보낸다.
            loop = asyncio.get_running_loop()
            downloaded = None
            if not _skips_trafilatura(target):
                downloaded = await loop.run_in_executor(
                    None,
                    functools.partial(trafilatura.fetch_url, target, config=self._trafilatura_config),
                )
            if downloaded and target != normalized:
                # PostView 문서에는 블로그 화면의 레이어 문구와 페이지 데이터가 함께 실려
                # 있어 trafilatura가 본문 앞뒤로 섞어 낸다. 본문 영역만 떼어 쓴다.
                body = BeautifulSoup(downloaded, "html.parser").select_one(".se-main-container")
                if body:
                    # 문단 안 강조 조각은 한 줄로 잇고, 편집기가 줄바꿈용으로 넣는
                    # 보이지 않는 공백(U+200B)만 있는 문단은 버린다.
                    paragraphs = (
                        " ".join(p.get_text().replace("​", "").split())
                        for p in body.find_all("p")
                    )
                    stripped = "\n".join(line for line in paragraphs if line)
                    if stripped:
                        return stripped[:limit] + ("..." if len(stripped) > limit else "")

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
                        return stripped[:limit] + ("..." if len(stripped) > limit else "")

            # 2차 fallback: BeautifulSoup
            status_code, body, content_type, final_url = await loop.run_in_executor(
                None, self._fetch_sync, target
            )

            if status_code != 200:
                return LinkFailure(f"[링크 읽기 실패: HTTP {status_code}]")

            if body.startswith(PDF_MAGIC):
                if len(body) > MAX_PDF_BYTES:
                    return LinkFailure(f"[PDF 용량 초과: {MAX_PDF_BYTES // (1024 * 1024)}MB 상한]")
                return PdfDocument(body)

            html = _decode_html(body, content_type)

            soup = BeautifulSoup(html, "html.parser")

            # 제목 추출 (meta 제거 전에)
            title = ""
            title_tag = soup.find("title")
            if title_tag:
                title = title_tag.get_text(strip=True)
            elif soup.find("h1"):
                title = soup.find("h1").get_text(strip=True)

            # og:description / meta description 추출 (meta 제거 전에)
            content = ""
            # DART 공시 본문은 표가 전부라 문단·요약 메타만 집으면 제목 한 줄만 남는다. 전체 텍스트로 간다.
            whole_text = _is_dart_viewer(final_url)
            og_desc = soup.find("meta", property="og:description")
            if not whole_text and og_desc and og_desc.get("content"):
                content = og_desc["content"].strip()

            if not content and not whole_text:
                meta_desc = soup.find("meta", attrs={"name": "description"})
                if meta_desc and meta_desc.get("content"):
                    content = meta_desc["content"].strip()

            # 이제 불필요한 태그 제거
            for tag in soup(["script", "style", "meta", "noscript"]):
                tag.decompose()

            # paragraph 추출
            if not content and not whole_text:
                paragraphs = soup.find_all("p", limit=5)
                if paragraphs:
                    content = "\n".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))

            # article 본문
            if not content and not whole_text:
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
                result += content[:limit] + ("..." if len(content) > limit else "")

            return result if result else LinkFailure("[내용을 읽을 수 없습니다]")

        except BlockedURLError as e:
            return LinkFailure(f"[차단된 URL: {e}]")
        except requests.exceptions.Timeout:
            return LinkFailure("[링크 읽기 타임아웃]")
        except requests.exceptions.ConnectionError:
            return LinkFailure("[연결 실패]")
        except Exception as e:
            return LinkFailure(f"[링크 읽기 오류: {str(e)[:50]}]")

    def _fetch_sync(self, url: str) -> tuple[int, bytes, str, str]:
        """
        동기 HTTP GET 요청을 수행합니다.

        이 메서드는 asyncio.run_in_executor에서 호출됩니다.

        리다이렉트를 requests에 맡기지 않고 직접 따라가며 홉마다
        `assert_public_url`을 다시 건다. 최종 목적지만 검사하면 공개 주소로
        시작해 내부 주소로 넘기는 리다이렉트를 막을 수 없다.

        Args:
            url: 요청할 URL (호출 전에 이미 1회 검증된 상태)

        Returns:
            (HTTP 상태코드, 본문 바이트, Content-Type, 마지막으로 요청한 URL) 튜플

        Raises:
            BlockedURLError: 리다이렉트 목적지가 비공개 대역이거나 홉 한도 초과
        """
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                         'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            # 리다이렉트 끝에서 만난 뷰어도 문서 주소로 바꾼다. 바꾼 주소도 아래에서 다시 검사한다.
            current = _rewrite_for_body(current)
            assert_public_url(current)
            response = requests.get(
                current,
                headers=headers,
                timeout=self.timeout,
                allow_redirects=False,
                stream=True,
            )
            location = response.headers.get("Location")
            if 300 <= response.status_code < 400 and location:
                response.close()
                current = urljoin(current, location)
                continue
            content_type = response.headers.get("Content-Type", "") or ""
            cap = MAX_PDF_BYTES if _may_be_pdf(content_type) else MAX_RESPONSE_BYTES
            body = self._read_capped_bytes(response, cap)
            if not body.startswith(PDF_MAGIC):
                body = body[:MAX_RESPONSE_BYTES]
            following = _next_hop(current, body) if response.status_code == 200 else None
            if following:
                current = following
                continue
            return response.status_code, body, content_type, current

        raise BlockedURLError(f"리다이렉트 {MAX_REDIRECTS}회 초과")

    @staticmethod
    def _read_capped(response: "requests.Response") -> str:
        """응답 본문을 `MAX_RESPONSE_BYTES`까지만 읽어 문자열로 만든다.

        본문 전체를 메모리에 올리면 거대한 응답 하나로 수집이 멈춘다. 어차피
        뒤에서 `MAX_CONTENT_CHARS`로 자르므로 앞부분만 있으면 충분하다.
        """
        body = LinkExtractor._read_capped_bytes(response, MAX_RESPONSE_BYTES)[:MAX_RESPONSE_BYTES]
        return body.decode("utf-8", errors="replace")

    @staticmethod
    def _read_capped_bytes(response: "requests.Response", cap: int) -> bytes:
        """응답 본문을 `cap` 바이트까지만 읽는다. 상한을 넘으면 `cap + 1`바이트를 돌려 잘렸음을 알린다."""
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            chunks.append(chunk)
            size += len(chunk)
            if size > cap:
                break
        response.close()
        return b"".join(chunks)[: cap + 1]

    async def _fetch_content_bounded(self, url: str) -> str:
        """`fetch_content`를 hard_timeout으로 감싼다.

        trafilatura·BeautifulSoup 단계가 자체 타임아웃을 지키지 못하고 멈추면
        수집 전체가 무기한 대기 상태가 되므로, URL 1건마다 상한을 강제한다.
        """
        try:
            return await asyncio.wait_for(self.fetch_content(url), timeout=self.hard_timeout)
        except asyncio.TimeoutError:
            return LinkFailure("[링크 읽기 타임아웃]")

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
                contents[url] = LinkFailure(f"[링크 읽기 오류: {str(result)[:50]}]")
            else:
                contents[url] = result

        return {"urls": urls, "contents": contents}
