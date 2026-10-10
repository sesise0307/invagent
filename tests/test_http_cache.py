"""공유 HTTP 응답 캐시를 스킬 스크립트 경로로 확인한다."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from invagent.datafeed import http as datafeed_http
from invagent.datafeed import naver as datafeed_naver
from invagent.datafeed import stockeasy as datafeed_stockeasy
from tests._helpers import _load_http_cache_module
from tests.conftest import SKILLS_ROOT, load_script

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_http_cache_writes_under_the_ignored_output_dir() -> None:
    """`output/`만 gitignore다 — 한 칸 어긋나면 캐시 바이너리가 추적 대상 경로에 쌓인다."""
    module = _load_http_cache_module()
    assert module.CACHE_ROOT == REPO_ROOT / "output" / ".cache" / "http"


def test_http_cache_round_trips_within_the_ttl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """같은 URL을 짧은 간격으로 두 번 부르는 경로(analyze-stock의 info-tab 중복)를 없애는 것이 목적이다."""
    module = _load_http_cache_module()
    monkeypatch.setattr(module, "CACHE_ROOT", tmp_path)

    url = "https://example.test/stock-info/info-tab/000660"
    assert module.load(url, authed=True) is None

    module.store(url, b'{"ok": 1}', authed=True)
    assert module.load(url, authed=True) == b'{"ok": 1}'


def test_http_cache_expires_and_never_mixes_auth_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """비인증 401 본문이 인증 호출에 재생되면 쿠키를 넣어도 계속 401로 보인다."""
    module = _load_http_cache_module()
    monkeypatch.setattr(module, "CACHE_ROOT", tmp_path)
    url = "https://example.test/stock-info/info-tab/000660"

    module.store(url, b"anon", authed=False)
    assert module.load(url, authed=True) is None
    assert module.load(url, authed=False) == b"anon"

    # TTL이 지나면 없는 것으로 취급한다.
    assert module.load(url, authed=False, ttl=0) is None


def test_http_cache_key_never_carries_the_cookie(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """캐시 경로도 파일 내용도 쿠키 값을 남기면 안 된다 — `output/`은 gitignore지만 평문 디스크다.

    실제 쿠키를 붙여 수집 경로를 통째로 한 번 태운 뒤, 캐시 루트 어디에도 그 값이 없는지 본다.
    """
    secret = "SESSIONID=super-secret-value-42"
    module = load_script("analyze-stock", "fetch_stock_info")
    monkeypatch.setattr(module.http_cache, "CACHE_ROOT", tmp_path)
    monkeypatch.setattr(datafeed_http, "read_url", lambda url, headers, timeout: b'{"ok": 1}')

    payload, err = datafeed_stockeasy.fetch_stock_json("/stock-info/info-tab/000660", cookie=secret)
    assert (payload, err) == ({"ok": 1}, None)

    written = list(tmp_path.rglob("*"))
    assert any(p.is_file() for p in written), "캐시가 쓰이지 않아 검사가 무의미하다"
    for path in written:
        assert secret not in str(path)
        if path.is_file():
            assert secret.encode() not in path.read_bytes()


def test_http_cache_evicts_stale_entries_once_per_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """purge()를 아무도 부르지 않으면 `output/.cache/`가 무한히 자란다.

    쓸 때 한 번만 쓸어내는 이유: 전수 스캔을 store()마다 돌리면 종목 수만큼 반복된다.
    """
    module = _load_http_cache_module()
    monkeypatch.setattr(module, "CACHE_ROOT", tmp_path)

    stale = tmp_path / "aa" / "aaaa.body"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")
    old_mtime = time.time() - module.RETENTION_SECONDS - 60
    os.utime(stale, (old_mtime, old_mtime))

    module.store("https://example.test/new", b"fresh", authed=False)

    assert not stale.exists(), "오래된 항목이 그대로 남았다"
    assert module.load("https://example.test/new", authed=False) == b"fresh"

    # 두 번째 쓰기는 다시 전수 스캔하지 않는다 — 프로세스당 한 번이 계약이다.
    later = tmp_path / "bb" / "bbbb.body"
    later.parent.mkdir(parents=True)
    later.write_bytes(b"old2")
    os.utime(later, (old_mtime, old_mtime))
    module.store("https://example.test/other", b"fresh2", authed=False)
    assert later.exists()


def test_http_cache_disable_does_not_leak_into_the_process_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--no-cache`가 프로세스 환경을 고치면 같은 프로세스의 뒤 작업까지 조용히 캐시를 잃는다.

    테스트에서도 `monkeypatch.delenv(raising=False)`는 **없던 변수**를 복원하지 않으므로
    한 테스트의 disable()이 뒤 테스트로 새어 나간다 — 통과 여부가 실행 순서에 걸린다.
    """
    module = _load_http_cache_module()
    monkeypatch.delenv(module.ENV_ENABLED, raising=False)

    module.disable()

    assert module.enabled() is False
    assert module.ENV_ENABLED not in os.environ, "프로세스 환경변수가 오염됐다"


def test_http_cache_can_be_switched_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """재현이 필요한 순간(캐시 오염 의심)에 끌 수 있어야 한다."""
    module = _load_http_cache_module()
    monkeypatch.setattr(module, "CACHE_ROOT", tmp_path)
    monkeypatch.setenv("INVAGENT_HTTP_CACHE", "0")

    module.store("https://example.test/x", b"body", authed=False)
    assert module.load("https://example.test/x", authed=False) is None


def test_daily_bars_and_market_signals_share_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """peak_drawdown은 종목마다, stage_scan은 종목마다 같은 네이버 일봉을 다시 받는다."""
    stage = load_script("stage-analysis", "stage_scan")
    monkeypatch.setattr(stage.http_cache, "CACHE_ROOT", tmp_path)

    body = "[['날짜'],\n['20260903', 1, 2, 3, 4, 5, 0.0]]".encode("utf-8")
    calls: list[str] = []

    def fake_read_url(url, headers, timeout):
        calls.append(url)
        return body

    monkeypatch.setattr(datafeed_http, "read_url", fake_read_url)
    first, err1 = datafeed_naver.fetch_bars("000660", 400)
    second, err2 = datafeed_naver.fetch_bars("000660", 400)
    assert (err1, err2) == (None, None)
    assert first == second and len(calls) == 1

    signals = load_script("daily-digest", "fetch_market_signals")
    monkeypatch.setattr(signals.http_cache, "CACHE_ROOT", tmp_path)
    sig_calls: list[str] = []

    def fake_signal_read_url(url, headers, timeout):
        sig_calls.append(url)
        return b'{"indices": []}'

    monkeypatch.setattr(datafeed_http, "read_url", fake_signal_read_url)
    assert signals.fetch_api("indices") == ({"indices": []}, None)
    assert signals.fetch_api("indices") == ({"indices": []}, None)
    assert len(sig_calls) == 1


def test_no_cache_flag_forces_a_fresh_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """캐시 오염이 의심될 때 껐다 확인할 수 있어야 한다 — 네트워크를 타는 스크립트 전부에 붙인다."""
    module = load_script("analyze-stock", "fetch_stock_info")
    monkeypatch.setattr(module.http_cache, "CACHE_ROOT", tmp_path)
    monkeypatch.delenv("INVAGENT_HTTP_CACHE", raising=False)

    calls: list[str] = []

    def fake_read_url(url, headers, timeout):
        calls.append(url)
        return b'{"ok": 1}'

    monkeypatch.setattr(datafeed_http, "read_url", fake_read_url)

    datafeed_stockeasy.fetch_stock_json("/stock-info/info-tab/064290")
    assert len(calls) == 1

    module.http_cache.disable()
    datafeed_stockeasy.fetch_stock_json("/stock-info/info-tab/064290")
    assert len(calls) == 2, "disable() 후에도 캐시가 응답했다"

    # 네트워크를 타는 스크립트 셋 모두 플래그를 노출하고 실제로 끈다.
    for name in ("analyze-stock/scripts/fetch_stock_info.py",
                 "stage-analysis/scripts/stage_scan.py",
                 "stage-analysis/scripts/turn_scan.py",
                 "daily-digest/scripts/peak_drawdown.py",
                 "daily-digest/scripts/fetch_market_signals.py"):
        text = (SKILLS_ROOT / name).read_text(encoding="utf-8")
        assert "--no-cache" in text, f"{name}에 --no-cache가 없다"
        assert "http_cache.disable()" in text, f"{name}이 플래그를 캐시에 연결하지 않았다"
