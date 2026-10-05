"""종목명 → 6자리 티커.

StockEasy `stock-search`가 못 잡는 신규 상장 종목이나, 시트 표기가 정식 종목명과 다른 경우를
`context/ticker_overrides.md`가 고정한다. 그 파일 자신이 "스킬 스크립트가 API보다 이 파일을
먼저 본다"고 규정하므로, 해석 경로는 한 곳뿐이어야 한다 — 예전에는 `peak_drawdown`만
오버라이드를 보고 `stage_scan`은 보지 않아 같은 질문에 답이 둘이었다.
"""

from __future__ import annotations

import re
from pathlib import Path

from invagent.datafeed import naver, stockeasy
from invagent.datafeed.env import repo_root

TICKER_RE = re.compile(r"^\d{6}$")
OVERRIDES_REL = Path("context") / "ticker_overrides.md"

# 종목코드 6자리의 마지막 자리는 주권 종류다 — 보통주 `0`, 구형 우선주 `5`·`7`·`9`,
# 신형 우선주(2013년 이후) `K`부터의 알파벳. 2026-09-21 DART 공시에 뜬 보통주 164종목이
# 예외 없이 `0`으로 끝났고, 삼성물산우B `02826K`의 본주가 `028260`(삼성물산)임도 같은
# 데이터로 교차검증했다.
CODE_LEN = 6
COMMON_LAST_CHAR = "0"


def common_code(code: str) -> str | None:
    """우선주 티커 → 본주 티커. 우선주가 아니거나 형식이 다르면 `None`.

    마지막 자리만 `0`으로 바꾼다. 구형(`005935`→`005930`)과 신형(`02826K`→`028260`) 둘 다
    같은 규칙이다. 앞 다섯 자리에 알파벳이 있는 신형 **보통주**(에임드바이오 `0009K0`)는
    `0`으로 끝나므로 손대지 않는다.
    """
    code = (code or "").strip()
    if len(code) != CODE_LEN or code.endswith(COMMON_LAST_CHAR):
        return None
    return code[: CODE_LEN - 1] + COMMON_LAST_CHAR


def overrides_path() -> Path:
    """오버라이드 파일의 기본 위치."""
    return repo_root() / OVERRIDES_REL


def load_overrides(path: Path | None = None) -> dict[str, str]:
    """`종목명 = 123456` 라인을 읽는다. 파일이 없으면 빈 맵.

    `#`·`>`·`-`로 시작하는 줄은 설명이므로 건너뛴다.
    """
    path = path or overrides_path()
    if not path.is_file():
        return {}

    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ">", "-")) or "=" not in line:
            continue
        name, _, code = line.partition("=")
        code = code.split("#")[0].strip()
        if TICKER_RE.match(code):
            out[name.strip()] = code
    return out


def resolve_code(name: str, overrides: dict[str, str] | None = None) -> tuple[str | None, str | None]:
    """종목명 → (티커, 실패 사유). override가 API보다 우선한다."""
    overrides = load_overrides() if overrides is None else overrides
    if name in overrides:
        return overrides[name], None
    hit, err, *_ = resolve_stock(name, overrides)
    if hit and hit.get("stock_code"):
        return hit["stock_code"], None
    return None, err or "티커 해석 실패"


def resolve_stock(query: str, overrides: dict[str, str] | None = None):
    """종목명 또는 티커 → (종목 레코드, 사유, exit_code). 오버라이드가 API보다 우선한다.

    `stockeasy.resolve_stock`과 같은 모양을 돌려주되 오버라이드 단계를 앞에 두고, 검색은 네이버가
    먼저다 — StockEasy 검색은 네이버가 실패했을 때만 부른다. 이름·거래소는
    오버라이드 파일에 없으므로 이름은 질의 그대로, 거래소는 미상으로 채운다.
    """
    overrides = load_overrides() if overrides is None else overrides
    if query in overrides:
        return {"stock_code": overrides[query], "stock_name": query, "exchange": None}, None, 0
    if TICKER_RE.match(query):
        return {"stock_code": query, "stock_name": None, "exchange": None}, None, 0
    hits, err = naver.search_stock(query)
    if err:
        # 네이버가 답하지 못할 때만 StockEasy 검색을 쓴다 — 요청 한도가 빠듯한 쪽이다.
        return stockeasy.resolve_stock(query)
    return stockeasy.select_hit(hits, query)


def fundamentals_code(code: str) -> tuple[str, bool]:
    """실적·컨센서스·뉴스 조회에 쓸 티커. `(티커, 우선주였나)`.

    우선주면 본주 티커를, 아니면 받은 티커를 그대로 돌려준다. **가격 축에는 쓰지 않는다** —
    우선주는 본주와 괴리율이 따로 움직이므로 일봉·이동평균·현재가는 우선주 자기 것을 써야 한다.

    우선주는 같은 회사의 다른 주식 종류라 분기 실적이 따로 없다. StockEasy `info-tab`을 우선주
    코드로 부르면 확정 분기가 비어 `stage_scan`이 가격 전용 판정으로 강등됐다 (2026-09-20
    삼성전자우). 판별은 `common_code`의 코드 축 단독이며 네트워크를 타지 않는다.
    """
    parent = common_code(code)
    return (parent, True) if parent else (code, False)
