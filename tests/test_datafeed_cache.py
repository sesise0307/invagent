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
