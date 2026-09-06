"""일봉 시리즈 도구.

`naver.fetch_bars`가 돌려주는 종가 배열 위에서 도는 순수 함수들. 판정 임계값은 여기 없다 —
150일선이 몇 일인지, 평탄을 몇 %로 볼지는 각 스킬의 운영 기준이다.
"""

from __future__ import annotations


def sma(values: list[float], window: int) -> list[float | None]:
    """단순이동평균. 구간이 안 차는 앞부분은 None."""
    out: list[float | None] = []
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= window:
            total -= values[i - window]
        out.append(total / window if i >= window - 1 else None)
    return out
