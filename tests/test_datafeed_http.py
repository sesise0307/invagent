"""`invagent.datafeed.http` — JSON API 한 번 호출."""

import json
import urllib.error

import pytest

from invagent.datafeed import cache, http


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path)


def test_get_json_decodes_the_body(monkeypatch) -> None:
    monkeypatch.setattr(http, "read_url", lambda url, headers, timeout: b'{"a": 1}')

    payload, err = http.get_json("https://example.test/x", authed=False)

    assert (payload, err) == ({"a": 1}, None)


def test_get_json_reports_the_status_code_instead_of_raising(monkeypatch) -> None:
    def boom(url, headers, timeout):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(http, "read_url", boom)

    payload, err = http.get_json("https://example.test/x", authed=True)

    assert payload is None
    assert err == "HTTP 401"


def test_get_json_refetches_once_when_a_cached_body_is_unparsable(monkeypatch) -> None:
    """캐시에 깨진 본문이 남아 있어도 한 번은 새로 받아 스스로 복구한다."""
    url = "https://example.test/y"
    cache.store(url, b"not json", authed=False)
    monkeypatch.setattr(http, "read_url", lambda u, headers, timeout: b'{"a": 2}')

    payload, err = http.get_json(url, authed=False)

    assert (payload, err) == ({"a": 2}, None)


def test_get_json_does_not_cache_a_failed_response(monkeypatch) -> None:
    def boom(url, headers, timeout):
        raise urllib.error.HTTPError(url, 500, "Server Error", {}, None)

    monkeypatch.setattr(http, "read_url", boom)
    http.get_json("https://example.test/z", authed=False)

    assert cache.load("https://example.test/z", authed=False) is None
