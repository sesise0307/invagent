"""호스트별 요청 제한 회피 — 공유 간격·동시 접속 상한, 재시도 백오프, 호스트 cool-down.

StockEasy는 짧은 시간에 요청이 몰리면 연결을 끊거나 거절한다. 서브에이전트 여러 개가
종목을 동시에 분석하면 프로세스끼리 서로를 모르고 두드리므로, 조절은 모든 요청이 지나는
`datafeed.http` 한 곳에서 한다. 간격과 동시 접속 수는 `STATE_ROOT`의 호스트별 잠금
파일(`fcntl.flock`)로 프로세스 사이에서 공유한다. 캐시 적중은 여기를 거치지 않는다.
"""

from __future__ import annotations

import json
import os
import random
import tempfile
import time
import urllib.error

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from invagent.datafeed.env import repo_root

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback; production runs on macOS/Linux.
    fcntl = None

# 저장소 루트 기준 `output/`은 gitignore 대상이다.
STATE_ROOT = repo_root() / "output" / ".cache" / "ratelimit"

# 같은 호스트로 나가는 요청 시작 사이의 최소 간격과 동시 요청 수 상한. 실제 한도는
# 공개돼 있지 않다 — 거절 로그를 보고 조정한다.
MIN_INTERVAL_SECONDS = 0.5
MAX_CONCURRENT_PER_HOST = 2

# 일시 거절로 보는 상태 코드. 401은 쿠키 문제라 재시도해도 같다.
RETRYABLE_STATUS = frozenset({429, 502, 503, 504})
MAX_ATTEMPTS = 3
# 첫 대기 1초, 이후 두 배. 서버가 `Retry-After`를 주면 그 값을 따르되 상한을 둔다.
BACKOFF_BASE_SECONDS = 1.0
MAX_WAIT_SECONDS = 30.0
# 재시도를 다 써도 거절되면 그 호스트는 이만큼 아무도 부르지 않는다. 한도에 걸린 채 계속
# 두드리면 차단이 길어지고, 그동안 일봉은 Naver 대체 경로로 간다.
COOLDOWN_SECONDS = 120.0
# cool-down 파일·메시지에 남기는 실패 사유의 최대 길이. 오류 문자열은 길이가 제각각이다.
REASON_MAX_CHARS = 80

# 호스트별로 기본값을 덮어쓰는 한도. StockEasy는 2026-10-05에 순차 실행으로도 분당 30~40건에서
# 연결을 끊었고, 2분 cool-down 뒤 다시 부르면 차단이 연장됐다 — 분당 20건 이하·한 번에 하나·
# 10분 대기로 잡는다.
HOST_LIMITS = {
    "stockeasy.intellio.kr": {"min_interval": 8.0, "max_concurrent": 1, "cooldown": 600.0},
}


def _limit(host: str, key: str, default: float) -> float:
    return HOST_LIMITS.get(host, {}).get(key, default)

_sleep = time.sleep
_now = time.time


class CoolingDown(Exception):
    """호스트가 cool-down 중이라 요청하지 않았다."""


def _jitter() -> float:
    # 여러 프로세스가 같은 순간 실패해도 같은 순간 다시 몰리지 않게 흩뜨린다.
    return random.uniform(0.0, 0.5)


def is_retryable(error: BaseException) -> bool:
    """제한·일시 장애로 보이는 실패인가."""
    if isinstance(error, urllib.error.HTTPError):
        return error.code in RETRYABLE_STATUS
    return isinstance(error, (urllib.error.URLError, ConnectionError, TimeoutError))


def call_with_retry(host: str, fetch):
    """`fetch()`를 재시도 가능한 실패에 한해 최대 MAX_ATTEMPTS번 부른다.

    호스트가 cool-down 중이면 부르지 않고 `CoolingDown`을 던진다. 재시도를 다 써도
    거절되면 호스트를 cool-down에 넣고 마지막 오류를 그대로 던진다.
    """
    remaining = cooldown_remaining(host)
    if remaining > 0:
        raise CoolingDown(f"{host} 요청 제한으로 대기 중 — {remaining:.0f}초 남음 · {cooldown_reason(host)}")
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with paced(host):
                return fetch()
        except Exception as error:
            if not is_retryable(error):
                raise
            if attempt == MAX_ATTEMPTS:
                cooldown = _limit(host, "cooldown", COOLDOWN_SECONDS)
                start_cooldown(host, max(cooldown, retry_after(error) or 0.0), reason=failure_reason(error))
                raise
            _sleep(wait_seconds(error, attempt))


def _cooldown_path(host: str) -> Path:
    return STATE_ROOT / f"{host}.cooldown"


def failure_reason(error: BaseException) -> str:
    """cool-down에 들어가게 한 실패를 한 줄로. 서버 거절(HTTP)과 연결 문제를 구분하는 게 목적이다."""
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}"
    if isinstance(error, TimeoutError) or isinstance(getattr(error, "reason", None), TimeoutError):
        return "시간 초과"
    if isinstance(error, urllib.error.URLError):
        return f"연결 끊김: {str(error.reason)[:REASON_MAX_CHARS]}"
    return f"연결 끊김: {str(error)[:REASON_MAX_CHARS]}"


def _read_cooldown(host: str) -> dict:
    """cool-down 파일. 옛 형식(해제 시각 숫자 하나)도 읽는다."""
    try:
        text = _cooldown_path(host).read_text()
    except OSError:
        return {}
    try:
        return {"until": float(text)}
    except ValueError:
        pass
    try:
        state = json.loads(text)
        return state if isinstance(state, dict) and isinstance(state.get("until"), (int, float)) else {}
    except ValueError:
        return {}


def cooldown_remaining(host: str) -> float:
    """cool-down이 풀리기까지 남은 초. 없으면 0."""
    until = _read_cooldown(host).get("until")
    return 0.0 if until is None else max(0.0, until - _now())


def cooldown_reason(host: str) -> str:
    """cool-down에 들어간 사유와 시각. 사유가 없는 옛 파일이면 「사유 미기록」."""
    state = _read_cooldown(host)
    reason = state.get("reason")
    if not reason:
        return "사유 미기록"
    started = state.get("started")
    at = f" ({time.strftime('%H:%M:%S', time.localtime(started))} 진입)" if started else ""
    return f"사유 {reason}{at}"


def start_cooldown(host: str, seconds: float, reason: str = "") -> None:
    """호스트를 seconds 동안 cool-down에 넣고 사유를 남긴다. 쓰기 실패는 무시한다 — 보조 장치다."""
    path = _cooldown_path(host)
    now = _now()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, raw_tmp = tempfile.mkstemp(prefix=f".{host}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "w") as handle:
            json.dump({"until": now + seconds, "started": now, "reason": reason}, handle, ensure_ascii=False)
        Path(raw_tmp).replace(path)
    except OSError:
        pass


def retry_after(error: BaseException) -> float | None:
    """HTTP 오류의 `Retry-After`(초). 없거나 날짜 형식이면 None."""
    headers = getattr(error, "headers", None)
    raw = headers.get("Retry-After") if headers else None
    try:
        return max(0.0, float(raw)) if raw is not None else None
    except ValueError:
        return None


def wait_seconds(error: BaseException, attempt: int) -> float:
    """attempt번째 실패 뒤 기다릴 시간."""
    hinted = retry_after(error)
    if hinted is not None:
        return min(hinted, MAX_WAIT_SECONDS)
    return min(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1) + _jitter(), MAX_WAIT_SECONDS)


@contextmanager
def _flock(path: Path, *, blocking: bool = True) -> Iterator[bool]:
    """잠금 파일 하나를 잡는다. 잡았으면 True. 잠금 자체가 실패하면 잡은 셈 친다."""
    if fcntl is None:
        yield True
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+")
    except OSError:
        yield True
        return
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        except OSError:
            yield True
            return
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


@contextmanager
def _slot(host: str) -> Iterator[None]:
    """동시 요청 슬롯 하나를 잡는다. 빈 슬롯이 없으면 임의의 슬롯을 기다린다."""
    slots = int(_limit(host, "max_concurrent", MAX_CONCURRENT_PER_HOST))
    paths = [STATE_ROOT / f"{host}.slot{i}" for i in range(slots)]
    for path in paths:
        with _flock(path, blocking=False) as held:
            if held:
                yield
                return
    with _flock(random.choice(paths)):
        yield


def _wait_for_turn(host: str) -> None:
    """마지막 요청 시작에서 호스트의 최소 간격이 지나도록 기다리고 지금을 기록한다."""
    path = STATE_ROOT / f"{host}.last"
    with _flock(STATE_ROOT / f"{host}.pace"):
        try:
            last = float(path.read_text())
        except (OSError, ValueError):
            last = None
        if last is not None:
            wait = last + _limit(host, "min_interval", MIN_INTERVAL_SECONDS) - _now()
            if wait > 0:
                _sleep(wait)
        try:
            path.write_text(str(_now()))
        except OSError:
            pass


@contextmanager
def paced(host: str) -> Iterator[None]:
    """한 요청의 구간 — 슬롯을 잡고, 간격을 지킨 뒤 요청한다."""
    with _slot(host):
        _wait_for_turn(host)
        yield
