"""JSON API 호출 한 벌.

`fetch_stock_info`·`fetch_market_signals`·`stage_scan`이 각자 들고 있던 요청 + 캐시 조회 +
캐시된 깨진 본문 복구 블록이 서로 15줄짜리 복붙이었고, 그중 하나는 `get_or_fetch`를 우회해
파일 락도 이중 확인도 없었다. 세 벌을 여기 하나로 모은다.

실패는 예외가 아니라 `(None, 사유)`로 돌려준다 — 수집 실패가 브리핑·보고서 작성을 막지
않는다는 것이 스킬들의 공통 규약이기 때문이다.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

from invagent.datafeed import cache, ratelimit

TIMEOUT = 20
USER_AGENT = "Mozilla/5.0"


def read_url(url: str, headers: dict[str, str], timeout: int) -> bytes:
    """URL 하나를 읽어 본문 바이트를 돌려준다. 테스트가 갈아끼우는 지점이다."""
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).hostname or "unknown"


def build_headers(
    *, referer: str | None = None, cookie: str | None = None, accept: str = "application/json"
) -> dict[str, str]:
    """요청 헤더. 쿠키는 있을 때만 붙는다 — 값은 어디에도 출력하지 않는다."""
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    if referer:
        headers["Referer"] = referer
    if cookie:
        headers["Cookie"] = cookie
    return headers


def get_bytes(
    url: str, *, authed: bool, headers: dict[str, str] | None = None, timeout: int = TIMEOUT
) -> tuple[bytes | None, str | None]:
    """본문 바이트를 캐시 경유로 받는다. 실패하면 `(None, 사유)`."""
    request_headers = headers if headers is not None else build_headers()

    def fetcher() -> bytes:
        return ratelimit.call_with_retry(_host(url), lambda: read_url(url, request_headers, timeout))

    try:
        body, _ = cache.get_or_fetch(url, authed=authed, fetcher=fetcher)
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except Exception as e:  # 네트워크 오류 등
        return None, str(e)[:80]
    return body, None


def get_json(
    url: str,
    *,
    authed: bool,
    headers: dict[str, str] | None = None,
    timeout: int = TIMEOUT,
    decode=json.loads,
    closed_from=None,
):
    """JSON 하나를 받아 파싱해 돌려준다. 실패하면 `(None, 사유)`.

    `closed_from`은 `cache.load`로 그대로 넘긴다 — 장 마감으로 보는 시각.

    캐시가 준 본문이 파싱되지 않으면 그 항목을 버리고 딱 한 번 다시 받는다. 이전 실행이
    남긴 오류 본문 하나가 TTL 내내 같은 실패를 되풀이하는 것을 막는다.
    """
    request_headers = headers if headers is not None else build_headers()

    def fetcher() -> bytes:
        return ratelimit.call_with_retry(_host(url), lambda: read_url(url, request_headers, timeout))

    try:
        body, cache_hit = cache.get_or_fetch(
            url, authed=authed, fetcher=fetcher, closed_from=closed_from
        )
        try:
            payload = decode(body.decode("utf-8"))
        except ValueError:
            if not cache_hit:
                raise
            cache.invalidate(url, authed=authed)
            body, _ = cache.get_or_fetch(url, authed=authed, fetcher=fetcher)
            payload = decode(body.decode("utf-8"))
    except Exception as e:  # HTTP 오류·네트워크 오류·JSON 파싱 실패 등
        stale = _stale_payload(url, authed, e, decode)
        if stale is not None:
            return stale, None
        return None, _reason(e)
    return payload, None


def _reason(error: Exception) -> str:
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}"
    if isinstance(error, ratelimit.CoolingDown):
        return str(error)  # 직접 만든 메시지 — 끝의 cool-down 사유가 잘리면 안 된다
    return str(error)[:80]


def _stale_payload(url: str, authed: bool, error: Exception, decode):
    """요청 제한으로 실패했으면 보관 중인 지난 응답을 꺼내고, 그 나이를 stderr에 밝힌다.

    401 같은 영구 실패에는 쓰지 않는다 — 쿠키 만료를 지난 값으로 덮으면 알아챌 수 없다.
    """
    if not (isinstance(error, ratelimit.CoolingDown) or ratelimit.is_retryable(error)):
        return None
    stale = cache.load_stale(url, authed=authed)
    if stale is None:
        return None
    body, age = stale
    try:
        payload = decode(body.decode("utf-8"))
    except ValueError:
        return None
    path = urllib.parse.urlsplit(url).path
    print(
        f"[캐시 대체] {_host(url)}{path} — 요청 제한({_reason(error)})으로 "
        f"{age / 60:.0f}분 전 응답을 쓴다",
        file=sys.stderr,
    )
    return payload
