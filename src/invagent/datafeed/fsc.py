"""금융위원회 주식시세정보 — KRX 공식 정규장 일봉.

공공데이터포털(`data.go.kr`)의 `GetStockSecuritiesInfoService_V2`. 종목 하나의 3년치 일봉을 한 번에
주고, 종가는 KRX 정규장 종가다 — 네이버가 시간외가를 섞어 95,600원으로 보인 대덕전자 2026-09-14를
97,000원으로 준다. 요청 한도는 개발계정 하루 10,000회라 StockEasy처럼 막히지 않는다.

데이터는 기준일 다음 영업일 13시 이후에 올라온다(T+1). 빠진 최근 봉은 `daily`가 채운다.

가격은 수정주가가 아니다 — LS ELECTRIC 2026-04-13 5:1 분할 전 봉이 788,000원 그대로라 150일선이
334,474원으로 잡혀 2단계 종목이 4단계로 판정됐다(2026-10-07). 그래서 `adjust_for_splits`가 맞춘다.
인증키는 `DATA_GO_KR_SERVICE_KEY`(환경변수 → `.env`)에서 읽고 어디에도 출력하지 않는다.
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import date, timedelta
from fractions import Fraction

from invagent.datafeed import http, naver
from invagent.datafeed.env import env_value

PRICE_URL = (
    "https://apis.data.go.kr/1160100/GetStockSecuritiesInfoService_V2/getStockPriceInfo_V2"
)
KEY_ENV = "DATA_GO_KR_SERVICE_KEY"
TIMEOUT = http.TIMEOUT


def load_key() -> str | None:
    """`DATA_GO_KR_SERVICE_KEY`를 환경변수 → 저장소 `.env` 순으로 찾는다. 값은 출력하지 않는다."""
    return env_value(KEY_ENV)


def parse_prices(text: str, code: str) -> list[dict]:
    """응답을 일봉 리스트로 바꾼다. 코드가 정확히 같은 행만, 날짜 오름차순으로."""
    data = json.loads(text)
    rejected = (data.get("OpenAPI_ServiceResponse") or {}).get("cmmMsgHeader")
    if rejected:
        # 키 오류·서비스 경로 오류도 HTTP 200으로 온다.
        raise ValueError(f"{rejected.get('errMsg')} (code {rejected.get('returnReasonCode')})")
    body = (data.get("response") or {}).get("body") or {}
    items = (body.get("items") or {}).get("item") or []
    bars = []
    for row in items:
        if row.get("srtnCd") != code:
            continue
        try:
            bars.append(
                {
                    "date": str(row["basDt"]),
                    "open": float(row["mkp"]),
                    "high": float(row["hipr"]),
                    "low": float(row["lopr"]),
                    "close": float(row["clpr"]),
                    "volume": float(row["trqu"]),
                    "change": float(row["vs"]) if row.get("vs") not in (None, "") else None,
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
        if bars[-1]["open"] == 0 and bars[-1]["volume"] == 0:
            # 매매정지일 — 체결이 없어 StockEasy·네이버 일봉에는 없는 날이다.
            bars.pop()
    bars.sort(key=lambda b: b["date"])
    return adjust_for_splits(bars)


def adjust_for_splits(bars: list[dict]) -> list[dict]:
    """분할·병합 같은 권리 조정을 이전 봉에 거꾸로 적용한다. 입력의 `change`(전일대비, 없으면 None)는 소비한다.

    KRX의 전일대비는 조정 기준가 대비다. 평소에는 `종가 - 전일대비`가 전일 종가와 같고, 조정일에만
    어긋난다. 그 비율(기준가 ÷ 전일 종가)을 그날 이전 봉의 가격에 곱하고 거래량은 나눈다.
    """
    ratios = [Fraction(1)] * len(bars)
    for i in range(len(bars) - 1, 0, -1):
        prev_close = Fraction(bars[i - 1]["close"])
        change = bars[i]["change"]
        base = Fraction(bars[i]["close"]) - Fraction(change) if change is not None else prev_close
        ratio = base / prev_close if prev_close > 0 and base > 0 else Fraction(1)
        ratios[i - 1] = ratios[i] * ratio
    adjusted = []
    for bar, ratio in zip(bars, ratios):
        out = {k: v for k, v in bar.items() if k != "change"}
        if ratio != 1:
            for field in ("open", "high", "low", "close"):
                out[field] = float(Fraction(bar[field]) * ratio)
            out["volume"] = float(Fraction(bar["volume"]) / ratio)
        adjusted.append(out)
    return adjusted


def fetch_bars(code: str, days: int, asof: date | str | None = None) -> tuple[list[dict], str | None]:
    """공식 일봉을 받아온다. 실패하면 ([], 사유)."""
    key = load_key()
    if not key:
        return [], f"{KEY_ENV} 미설정"
    end = naver.parse_date(asof, "asof") if asof is not None else date.today()
    start = end - timedelta(days=days)
    query = urllib.parse.urlencode(
        {
            "serviceKey": key,
            "resultType": "json",
            "likeSrtnCd": code,
            "beginBasDt": start.strftime("%Y%m%d"),
            "endBasDt": end.strftime("%Y%m%d"),
            "numOfRows": max(days, 1),
        }
    )
    bars, err = http.get_json(
        f"{PRICE_URL}?{query}",
        authed=True,
        timeout=TIMEOUT,
        decode=lambda text: parse_prices(text, code),
    )
    return bars or [], err
