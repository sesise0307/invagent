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
