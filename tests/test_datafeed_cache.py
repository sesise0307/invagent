"""`invagent.datafeed.cache` — 짧은 TTL HTTP 응답 캐시."""

from invagent.datafeed import cache, env


def test_cache_root_sits_under_the_gitignored_output_dir() -> None:
    """`output/`만 gitignore된다. 캐시가 그 밖으로 나가면 저장소에 섞인다."""
    assert cache.CACHE_ROOT == env.repo_root() / "output" / ".cache" / "http"


def test_get_or_fetch_serves_the_second_call_from_the_cache(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path)
    calls = []

    def fetcher() -> bytes:
        calls.append(1)
        return b"payload"

    first, first_hit = cache.get_or_fetch("https://example.test/a", authed=False, fetcher=fetcher)
    second, second_hit = cache.get_or_fetch("https://example.test/a", authed=False, fetcher=fetcher)

    assert (first, second) == (b"payload", b"payload")
    assert (first_hit, second_hit) == (False, True)
    assert len(calls) == 1


def test_the_authenticated_and_anonymous_axes_never_share_an_entry(tmp_path, monkeypatch) -> None:
    """무인증 401 본문이 인증 호출로 재생되면 안 된다."""
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path)
    cache.store("https://example.test/b", b"anon", authed=False)

    assert cache.load("https://example.test/b", authed=True) is None
    assert cache.load("https://example.test/b", authed=False) == b"anon"


from datetime import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

import pytest  # noqa: E402

KST = ZoneInfo("Asia/Seoul")
QUOTE_URL = "https://stockeasy.intellio.kr/stockdata/api/v1/stock-info/info-tab/005930"
NEWS_URL = "https://stockeasy.intellio.kr/stockdata/api/v1/news/by-stock-code/005930"


def _kst(*parts) -> float:
    return datetime(*parts, tzinfo=KST).timestamp()


@pytest.mark.parametrize(
    ("url", "stored", "now", "fresh"),
    [
        # 금요일 NXT 마감(20:00) 뒤 받은 시세는 월요일 개장까지 그대로다.
        (QUOTE_URL, _kst(2026, 10, 2, 20, 30), _kst(2026, 10, 3, 12, 0), True),
        (QUOTE_URL, _kst(2026, 10, 2, 20, 30), _kst(2026, 10, 5, 8, 59), True),
        (QUOTE_URL, _kst(2026, 10, 2, 20, 30), _kst(2026, 10, 5, 9, 1), False),
        # 개장 전 새벽에 받은 시세는 그날 09:00까지.
        (QUOTE_URL, _kst(2026, 10, 1, 6, 0), _kst(2026, 10, 1, 8, 50), True),
        (QUOTE_URL, _kst(2026, 10, 1, 6, 0), _kst(2026, 10, 1, 9, 1), False),
        # 장중과 시간외(~20:00)는 15분 그대로.
        (QUOTE_URL, _kst(2026, 10, 1, 14, 0), _kst(2026, 10, 1, 14, 20), False),
        (QUOTE_URL, _kst(2026, 10, 1, 17, 0), _kst(2026, 10, 1, 17, 20), False),
        # 뉴스·공시는 마감 뒤에도 새로 나온다 — 연장하지 않는다.
        (NEWS_URL, _kst(2026, 10, 2, 21, 0), _kst(2026, 10, 2, 21, 30), False),
        ("https://api.finance.naver.com/siseJson.naver?symbol=005930", _kst(2026, 10, 2, 21, 0),
         _kst(2026, 10, 3, 9, 0), True),
    ],
)
def test_quotes_fetched_after_the_close_stay_fresh_until_the_next_open(
    tmp_path, monkeypatch, url, stored, now, fresh
) -> None:
    """마감 뒤 시세는 바뀌지 않는다. 저녁·새벽 재실행이 같은 값을 다시 받으면 한도만 깎인다."""
    import os

    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path)
    monkeypatch.delenv(cache.ENV_TTL, raising=False)
    monkeypatch.setattr(cache, "_now", lambda: now)
    cache.store(url, b"body", authed=True)
    os.utime(cache.cache_path(url, authed=True), (stored, stored))

    assert (cache.load(url, authed=True) is not None) is fresh
