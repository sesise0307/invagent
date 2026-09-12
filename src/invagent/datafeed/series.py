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


def swing_pivots(
    bars: list[dict], k: int, high_key: str = "high", low_key: str = "low"
) -> tuple[list[dict], list[dict]]:
    """좌우 k봉 프랙탈 고점·저점. 최근 k봉은 오른쪽 창이 안 차서 확정되지 않아 빠진다.

    기본은 장중 고가·저가로 잡는다. 종가 기준 판정이면 두 키를 `"close"`로 넘긴다.
    """
    highs, lows = [], []
    for i in range(k, len(bars) - k):
        window = bars[i - k : i + k + 1]
        if bars[i][high_key] >= max(b[high_key] for b in window):
            highs.append({"date": bars[i]["date"], "price": bars[i][high_key], "index": i})
        if bars[i][low_key] <= min(b[low_key] for b in window):
            lows.append({"date": bars[i]["date"], "price": bars[i][low_key], "index": i})
    return highs, lows
