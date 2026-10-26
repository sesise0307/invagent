"""OpenDART 공시검색 — 기간 안 전체 상장사 공시 목록.

`list.json`을 회사 지정 없이 부르면 기간 안 모든 공시를 접수 역순으로 준다(기간은 최대 3개월, 페이지당
100건). 보유·관심 종목 수십 개를 회사마다 따로 묻는 대신 몇 페이지로 하루치를 받아 티커로 거른다 —
2026-10-23~26 두 영업일이 1,076건·11페이지였다. 본문·공시 종류별 상세는 여기서 읽지 않는다. 그것은
`opendart` MCP의 몫이고, 이 모듈은 텔레그램 포워드로 오지 않은 공시가 있는지만 알려 준다.

인증키는 `OPENDART_API_KEY`(환경변수 → `.env`)에서 읽고 어디에도 출력하지 않는다 — MCP 서버에
넘기는 그 키다. 요청 URL에 키가 들어가지만 캐시는 URL을 해시로만 남긴다.
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import date

from invagent.datafeed import http
from invagent.datafeed.env import env_value

LIST_URL = "https://opendart.fss.or.kr/api/list.json"
KEY_ENV = "OPENDART_API_KEY"
PAGE_COUNT = 100
OK = "000"
NO_DATA = "013"


def load_key() -> str | None:
    """`OPENDART_API_KEY`를 환경변수 → 저장소 `.env` 순으로 찾는다. 값은 출력하지 않는다."""
    return env_value(KEY_ENV)


def _page_url(key: str, begin: date, end: date, page_no: int) -> str:
    query = urllib.parse.urlencode(
        {
            "crtfc_key": key,
            "bgn_de": begin.strftime("%Y%m%d"),
            "end_de": end.strftime("%Y%m%d"),
            "page_no": page_no,
            "page_count": PAGE_COUNT,
        }
    )
    return f"{LIST_URL}?{query}"


def _decode(text: str) -> dict:
    """응답 본문 → dict. 키 오류·한도 초과도 HTTP 200으로 오므로 status로 거른다.

    `013`(조회된 데이터 없음)은 실패가 아니라 빈 목록이다.
    """
    data = json.loads(text)
    status = data.get("status")
    if status == NO_DATA:
        return {"list": [], "total_page": 1}
    if status != OK:
        raise ValueError(f"OpenDART {status} {data.get('message', '')}".strip())
    return data


def fetch_filings(begin: date, end: date) -> tuple[list[dict] | None, str | None]:
    """`begin`~`end`(양끝 포함) 전체 공시. 실패하면 `(None, 사유)`."""
    key = load_key()
    if not key:
        return None, f"{KEY_ENV} 없음"
    filings: list[dict] = []
    page_no, total_page = 1, 1
    while page_no <= total_page:
        data, err = http.get_json(
            _page_url(key, begin, end, page_no), authed=False, decode=_decode
        )
        if err:
            return None, err
        filings.extend(data.get("list") or [])
        total_page = int(data.get("total_page") or 1)
        page_no += 1
    return filings, None
