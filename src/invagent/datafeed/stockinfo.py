"""StockEasy `info-tab` 두 축 — 시세 축(종목 자기 것)과 실적 축(본주 것).

우선주는 실적이 따로 없어 실적 축을 본주 코드로 받아야 하고, 시세 축은 괴리율이 따로 움직이므로
우선주 자기 코드로 받아야 한다. 스크립트마다 쿠키 로딩 → 호출 → 401 힌트 → 우선주 분기 → 출처
꼬리표를 손으로 되풀이하던 것을 이 모듈 하나가 맡는다.

두 축은 처음 읽힐 때 받는다. 보통주는 두 축이 같은 응답이라 요청 하나로 끝나고, 실적 축만 읽는
스크립트는 우선주 자기 `info-tab`을 받지 않는다. 실패 사유는 `gaps`, 출처는 `sources`에 쌓여
스크립트는 그 문구를 그대로 찍는다. `STOCKEASY_COOKIE`가 없으면 무인증 요청은 반드시 401이므로
보내지 않고, 401을 받았을 때와 같은 사유(`HTTP 401 (STOCKEASY_COOKIE 미설정)`)만 남긴다.
"""

from __future__ import annotations

from invagent.datafeed import stockeasy, tickers


def fetch(ticker: str) -> StockInfo:
    """6자리 티커 하나의 `StockInfo`. 이 호출 자체는 네트워크를 타지 않는다."""
    return StockInfo(ticker)


class StockInfo:
    """한 종목의 `info-tab` 두 축. 각 축은 처음 읽힐 때 한 번 받는다."""

    def __init__(self, ticker: str) -> None:
        self.ticker = ticker
        fs_code, self.is_preferred = tickers.fundamentals_code(ticker)
        self.parent_code: str | None = fs_code if self.is_preferred else None
        self.gaps: list[str] = []
        self.sources: list[str] = []
        self._payloads: dict[str, dict | None] = {}
        self._cookie: str | None = None
        self._cookie_loaded = False

    # --- 축 ---------------------------------------------------------------

    @property
    def fundamentals(self) -> dict | None:
        """실적 축 — 본주의 `info-tab` (보통주면 자기 것). 실패하면 None."""
        return self._load(self.parent_code or self.ticker)

    @property
    def price(self) -> dict | None:
        """시세 축 — 종목 자기 `info-tab`. 실패하면 None."""
        return self._load(self.ticker)

    @property
    def parent_name(self) -> str | None:
        """본주 이름 — 실적 축을 이미 받았을 때만. 이 속성이 요청을 일으키지는 않는다."""
        if not self.is_preferred:
            return None
        payload = self._payloads.get(self.parent_code)
        return ((payload or {}).get("stock_info") or {}).get("name")

    @property
    def name(self) -> str | None:
        """종목 자기 이름 — 자기 `info-tab`을 이미 받았을 때만. 보통주는 실적 축이 곧 자기 것이다.

        우선주는 시세 축을 받기 전엔 None이다. 본주 이름을 우선주 이름으로 내지 않으며, 이 속성이
        요청을 일으키지도 않는다.
        """
        payload = self._payloads.get(self.ticker)
        return ((payload or {}).get("stock_info") or {}).get("name")

    # --- 실적 시계열 ---------------------------------------------------------

    @property
    def primary_fs_type(self) -> str:
        """실적 축의 주 재무제표 — `C`(연결) 또는 `S`(별도). 모르면 연결."""
        return (self.fundamentals or {}).get("primary_fs_type") or "C"

    def rows(self, *, yearly: bool = False, estimates: bool = False) -> list[dict]:
        """실적 축 주 재무제표의 행 그대로 — 한 기간의 여러 값을 함께 쓰는 표용. 기본은 분기 확정."""
        financials = (self.fundamentals or {}).get("financials")
        if not financials:
            return []
        actual, estimate = stockeasy._fs_rows(financials, self.primary_fs_type, yearly=yearly)
        return estimate if estimates else actual

    def quarterly(self, field: str, *, estimates: bool = False) -> dict[tuple[int, int], float]:
        """실적 축 주 재무제표(C/S)의 분기 `{(연도, 분기): 값}`. 기본은 확정, `estimates`면 추정."""
        return self._series(field, yearly=False, estimates=estimates)

    def yearly(self, field: str, *, estimates: bool = False) -> dict[tuple[int, None], float]:
        """실적 축 주 재무제표(C/S)의 연간 `{(연도, None): 값}`. 기본은 확정, `estimates`면 추정."""
        return self._series(field, yearly=True, estimates=estimates)

    def _series(self, field: str, *, yearly: bool, estimates: bool) -> dict:
        values = {}
        for row in self.rows(yearly=yearly, estimates=estimates):
            value, year = row.get(field), row.get("year")
            quarter = None if yearly else row.get("quarter")
            if value is None or not year or (not yearly and not quarter):
                continue
            values[(year, quarter)] = value
        return values

    # --- 내부 ---------------------------------------------------------------

    def _load(self, code: str) -> dict | None:
        if code in self._payloads:
            return self._payloads[code]
        if not self._cookie_loaded:
            self._cookie = stockeasy.load_cookie()
            self._cookie_loaded = True
        if not self._cookie:
            # 무인증 info-tab은 반드시 401이다. 요청 예산을 쓰지 않고 401과 같은 사유를 남긴다.
            self._payloads[code] = None
            self.gaps.append(stockeasy.gap_reason("HTTP 401", None))
            return None
        payload, err = stockeasy.fetch_stock_json(
            stockeasy.ENDPOINTS["info_tab"].format(code=code),
            referer=f"{stockeasy.PAGE_BASE}/{code}",
            cookie=self._cookie,
        )
        payload = payload or None
        self._payloads[code] = payload
        if payload is None:
            self.gaps.append(stockeasy.gap_reason(err, self._cookie))
        else:
            self._note_source(code, payload)
        return payload

    def _note_source(self, code: str, payload: dict) -> None:
        if self.is_preferred and code == self.parent_code:
            name = (payload.get("stock_info") or {}).get("name")
            label = f"{name}({code})" if name else code
            self.sources.append(f"우선주 실적은 본주 {label}")
        if "StockEasy info-tab" not in self.sources:
            self.sources.append("StockEasy info-tab")
