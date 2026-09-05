#!/usr/bin/env python3
"""스킬 스크립트들이 공유하는 짧은 수명의 HTTP 응답 캐시.

한 번의 `analyze-stock` 실행은 StockEasy `info-tab`(약 128KB)을 두 번 받는다 —
`fetch_stock_info.py`가 한 번, 이어서 `stage_scan.py`가 다시 한 번. `peak_drawdown.py`도
`stage_scan.fetch_bars`를 종목마다 부르고, 브리핑을 다시 돌리면 같은 호출이 통째로 반복된다.
이 모듈은 그 중복만 없앤다.

**TTL이 짧은 이유**: `info-tab`에는 현재가가 들어 있다. 하루 단위로 캐시하면 장 마감 무렵
재실행이 아침 시세를 그대로 되돌려준다. 기본 15분은 「같은 실행 안의 중복」과 「직후 재실행」을
덮으면서 시세가 썩지 않는 폭이다. `INVAGENT_HTTP_CACHE_TTL`(초)로 조정하고,
`INVAGENT_HTTP_CACHE=0`이면 읽기·쓰기 모두 하지 않는다.

**인증 상태를 키에 섞는 이유**: 비인증 호출은 HTTP 401을 받는다. 그 응답을 쿠키가 붙은
호출에 되돌려주면 쿠키를 고쳐도 계속 401로 보인다. 키는 쿠키의 **유무**만 반영하고
쿠키 값은 키에도 파일에도 남기지 않는다.

실패 응답은 캐시하지 않는다 — 일시적인 401·타임아웃이 TTL 동안 고착되면 안 된다.

의존성: stdlib만 사용.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

# 저장소 루트 기준 `output/`은 gitignore 대상이라 새 추적 경로를 만들지 않는다.
# parents: [0] scripts · [1] analyze-stock · [2] skills · [3] .agents · [4] 저장소 루트.
CACHE_ROOT = Path(__file__).resolve().parents[4] / "output" / ".cache" / "http"

DEFAULT_TTL_SECONDS = 900
# TTL이 지난 항목도 파일로는 남는다. 하루 지난 것은 다시 쓰일 일이 없으므로 지운다.
RETENTION_SECONDS = 86_400
ENV_ENABLED = "INVAGENT_HTTP_CACHE"
ENV_TTL = "INVAGENT_HTTP_CACHE_TTL"


# `--no-cache`가 세운 플래그. 환경변수를 고치지 않는 이유: 프로세스 환경을 건드리면 같은
# 프로세스에서 이어지는 다른 작업(테스트 포함)까지 조용히 캐시를 잃고, 그 영향이 실행 순서에
# 따라 달라진다. 자식 프로세스에 끄기를 전달하는 쪽은 환경변수를 **넘겨서** 한다
# (`invagent daily-prep`의 `Step.env`).
_forced_off = False


def enabled() -> bool:
    """`INVAGENT_HTTP_CACHE=0`이거나 `disable()`을 불렀으면 캐시를 쓰지 않는다."""
    if _forced_off:
        return False
    return os.environ.get(ENV_ENABLED, "1").strip() not in {"0", "false", "no"}


def disable() -> None:
    """이 프로세스에서 캐시를 끈다. 스크립트의 `--no-cache`가 부른다."""
    global _forced_off
    _forced_off = True


def ttl_seconds() -> int:
    raw = os.environ.get(ENV_TTL, "")
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_TTL_SECONDS


def cache_path(url: str, *, authed: bool) -> Path:
    """URL + 인증 유무의 해시. 쿠키 값은 들어가지 않는다."""
    key = f"{'auth' if authed else 'anon'}|{url}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return CACHE_ROOT / digest[:2] / f"{digest}.body"


def load(url: str, *, authed: bool, ttl: int | None = None) -> bytes | None:
    """TTL 안의 응답 본문. 없거나 만료면 None."""
    if not enabled():
        return None
    path = cache_path(url, authed=authed)
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return None
    if age > (ttl_seconds() if ttl is None else ttl):
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


_purged = False


def store(url: str, body: bytes, *, authed: bool) -> None:
    """성공 응답만 넣는다. 캐시 쓰기 실패는 조용히 무시한다 — 수집을 막을 이유가 없다."""
    global _purged
    if not enabled():
        return
    # 첫 쓰기에서 한 번만 쓸어낸다. store()마다 전수 스캔하면 종목 수만큼 반복된다.
    if not _purged:
        _purged = True
        purge()
    path = cache_path(url, authed=authed)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)
    except OSError:
        pass


def purge(older_than: int = RETENTION_SECONDS) -> int:
    """오래된 항목을 지운다. 지운 개수를 반환한다."""
    now, removed = time.time(), 0
    if not CACHE_ROOT.exists():
        return 0
    for path in CACHE_ROOT.rglob("*.body"):
        try:
            if now - path.stat().st_mtime > older_than:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed
