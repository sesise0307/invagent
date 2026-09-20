"""종목명 → 6자리 티커.

StockEasy `stock-search`가 못 잡는 신규 상장 종목이나, 시트 표기가 정식 종목명과 다른 경우를
`context/ticker_overrides.md`가 고정한다. 그 파일 자신이 "스킬 스크립트가 API보다 이 파일을
먼저 본다"고 규정하므로, 해석 경로는 한 곳뿐이어야 한다 — 예전에는 `peak_drawdown`만
오버라이드를 보고 `stage_scan`은 보지 않아 같은 질문에 답이 둘이었다.
"""

from __future__ import annotations

import re
from pathlib import Path

from invagent.datafeed import stockeasy
from invagent.datafeed.env import repo_root

TICKER_RE = re.compile(r"^\d{6}$")
OVERRIDES_REL = Path("context") / "ticker_overrides.md"

# 우선주 종목명 접미사 — `우`, `우B`, `2우B`, `3우B`. 본주 이름은 그 앞까지다.
PREFERRED_SUFFIX_RE = re.compile(r"^(?P<base>.+?)\d*우B?$")


def common_name(name: str) -> str | None:
    """우선주 종목명 → 본주 종목명. 우선주가 아니면 `None`.

    우선주는 같은 회사의 다른 주식 종류일 뿐이라 분기 매출·영업이익이 따로 없다. StockEasy
    `info-tab`을 우선주 코드로 부르면 확정 분기 실적이 비어 `stage_scan`이 가격 전용 판정으로
    강등된다 — 실적 축은 본주 코드로 따로 받아야 한다 (2026-09-20 삼성전자우, 사용자 확정).

    판별은 **종목명 접미사**로 한다. `삼성전자(우)`처럼 시트 표기가 다르면 잡히지 않고,
    그때는 실적 축이 예전처럼 비어 판정이 가격 전용으로 남는다 — 틀린 본주를 붙이는 것보다 낫다.
    """
    m = PREFERRED_SUFFIX_RE.match(name.strip())
    if not m:
        return None
    base = m.group("base").strip()
    return base or None


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
    hit, err, *_ = stockeasy.resolve_stock(name)
    if hit and hit.get("stock_code"):
        return hit["stock_code"], None
    return None, err or "티커 해석 실패"


def resolve_stock(query: str, overrides: dict[str, str] | None = None):
    """종목명 또는 티커 → (종목 레코드, 사유, exit_code). 오버라이드가 API보다 우선한다.

    `stockeasy.resolve_stock`과 같은 모양을 돌려주되 오버라이드 단계를 앞에 둔다. 이름·거래소는
    오버라이드 파일에 없으므로 이름은 질의 그대로, 거래소는 미상으로 채운다.
    """
    overrides = load_overrides() if overrides is None else overrides
    if query in overrides:
        return {"stock_code": overrides[query], "stock_name": query, "exchange": None}, None, 0
    return stockeasy.resolve_stock(query)


def fundamentals_code(
    code: str, name: str | None, overrides: dict[str, str] | None = None
) -> tuple[str, str | None]:
    """실적·컨센·뉴스 조회에 쓸 티커. `(티커, 본주명 or None)`.

    우선주면 본주 티커를, 아니면 받은 티커를 그대로 돌려준다. **가격 축에는 쓰지 않는다** —
    우선주는 본주와 괴리율이 따로 움직이므로 일봉·이동평균·시세는 우선주 자기 것을 써야 한다.

    판별은 두 축이 모두 맞을 때만 성립한다.

    1. 종목명이 우선주 접미사로 끝난다 (`common_name`).
    2. 티커 끝자리가 `0`이 아니다 — KRX 관례상 보통주 코드는 0으로 끝난다.

    둘째 축은 이름이 '우'로 끝나는 **본주**를 거르기 위한 것이다 (미래에셋대우 006800). 본주
    해석에 실패하면 받은 티커를 그대로 돌려주고, 호출자는 예전처럼 가격 전용 판정으로 남는다.
    """
    if not name or code.endswith("0"):
        return code, None
    base = common_name(name)
    if not base:
        return code, None
    parent, _ = resolve_code(base, overrides)
    if not parent or parent == code:
        return code, None
    return parent, base
