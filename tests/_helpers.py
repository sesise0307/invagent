"""여러 테스트 파일이 함께 쓰는 보조 함수."""


def _load_http_cache_module():
    """캐시는 스킬 스크립트가 아니라 패키지 모듈이다 — 평범하게 import한다.

    `importlib.reload`로 새 모듈 객체를 돌려주는 이유: 이 아래 테스트들이 모듈 전역
    상태(`_forced_off`·`_purged`·`CACHE_ROOT`)를 건드리므로, 파일 로더가 매번 새
    모듈을 주던 예전 동작을 유지해야 서로 새지 않는다.
    """
    import importlib

    from invagent.datafeed import cache

    # 다시 읽으면 `CACHE_ROOT`가 실제 경로로 돌아간다 — conftest가 가리킨 임시 디렉터리를 지킨다.
    root = cache.CACHE_ROOT
    module = importlib.reload(cache)
    module.CACHE_ROOT = root
    return module
