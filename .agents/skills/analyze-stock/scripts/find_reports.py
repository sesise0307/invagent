#!/usr/bin/env python3
"""로컬 증권 리포트 아카이브에서 종목별 PDF를 찾아 최신순으로 출력한다.

아카이브 구조:
    <root>/<초성>/<종목명>/*.pdf          예) ㅇ/율촌화학/20260327_....pdf
    <root>/<초성>/<종목명>/IR자료/*.pdf
    <root>/A-Z/<종목명>/*.pdf             한글로 시작하지 않는 종목
    <root>/_0_To_Read/*.pdf               미분류 (파일명 매칭으로 보조 검색)
    <root>/_산업분석/<섹터>/*.pdf         섹터 리포트 (파일명 매칭으로 보조 검색)

기본 경로는 ~/1_Investment/리포트 이며 INVAGENT_REPORT_ARCHIVE 로 덮어쓸 수 있다.
표준 라이브러리만 사용한다.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

DEFAULT_ARCHIVE = Path.home() / "1_Investment" / "리포트"

# 유니코드 한글 음절 초성 19자
_CHOSUNG = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
# 아카이브에는 쌍자음 폴더가 없다. 평자음으로 접는다.
_FOLD = {"ㄲ": "ㄱ", "ㄸ": "ㄷ", "ㅃ": "ㅂ", "ㅆ": "ㅅ", "ㅉ": "ㅈ"}
# 한글로 시작하지 않는 종목(SK하이닉스, 3S, CJ 등)이 모이는 폴더
LATIN_DIR = "A-Z"
# 종목 폴더가 아니라 파일명 매칭으로만 훑는 특수 폴더 접두사
AUX_PREFIX = "_"


def nfc(text: str) -> str:
    """macOS 파일명은 NFD로 저장된다. 비교 전 NFC로 정규화한다."""
    return unicodedata.normalize("NFC", text)


def archive_root() -> Path:
    """아카이브 루트. 환경변수 우선."""
    override = os.environ.get("INVAGENT_REPORT_ARCHIVE")
    return Path(override).expanduser() if override else DEFAULT_ARCHIVE


def chosung_dir(name: str) -> str:
    """종목명 첫 글자의 초성 폴더 이름. 한글이 아니면 'A-Z'."""
    first = nfc(name).strip()[:1]
    if not first:
        return LATIN_DIR
    code = ord(first) - 0xAC00
    if 0 <= code <= 11171:
        initial = _CHOSUNG[code // 588]
        return _FOLD.get(initial, initial)
    if first in _CHOSUNG:
        return _FOLD.get(first, first)
    return LATIN_DIR


def _key(name: str) -> str:
    """비교용 키 — NFC + 소문자 + 공백/구두점 제거."""
    return re.sub(r"[\s._·\-]+", "", nfc(name).lower())


def find_stock_dirs(root: Path, query: str) -> tuple[list[Path], str]:
    """종목 폴더 후보와 매칭 방식(exact/fuzzy/fallback/none)을 돌려준다."""
    if not root.is_dir():
        return [], "none"

    target = _key(query)
    primary = root / chosung_dir(query)
    search_order = [primary] + [
        d
        for d in sorted(root.iterdir())
        if d.is_dir() and d != primary and not d.name.startswith(AUX_PREFIX)
    ]

    exact: list[Path] = []
    fuzzy: list[Path] = []
    for parent in search_order:
        if not parent.is_dir():
            continue
        for candidate in sorted(parent.iterdir()):
            if not candidate.is_dir():
                continue
            key = _key(candidate.name)
            if key == target:
                exact.append(candidate)
            elif target in key or key in target:
                fuzzy.append(candidate)

    if exact:
        return exact, "exact"
    if fuzzy:
        kind = "fuzzy" if fuzzy[0].parent == primary else "fallback"
        return fuzzy, kind
    return [], "none"


def find_aux_files(root: Path, query: str) -> list[Path]:
    """`_`로 시작하는 특수 폴더에서 파일명에 종목명이 든 PDF를 모은다."""
    if not root.is_dir():
        return []
    target = _key(query)
    found: list[Path] = []
    for parent in sorted(root.iterdir()):
        if not parent.is_dir() or not parent.name.startswith(AUX_PREFIX):
            continue
        for pdf in parent.rglob("*.pdf"):
            if target in _key(pdf.name):
                found.append(pdf)
    return found


def parse_date(filename: str) -> str | None:
    """파일명 선두 날짜 토큰을 ISO 문자열로. 없으면 None.

    20260327_... -> 2026-03-27 / 202602_... -> 2026-02 / 2020_... -> 2020
    """
    name = nfc(filename)
    match = re.match(r"^(\d{4})(\d{2})?(\d{2})?(?=\D|$)", name)
    if not match:
        return None
    year, month, day = match.group(1), match.group(2), match.group(3)
    if not 1990 <= int(year) <= 2100:
        return None
    if month is None:
        return year
    if not 1 <= int(month) <= 12:
        return None
    if day is None:
        return f"{year}-{month}"
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None


def _sort_key(item: tuple[str | None, Path]) -> tuple[int, str, str]:
    parsed, path = item
    # 날짜 있는 파일이 먼저, 그 안에서 최신순. 날짜 없으면 이름 역순.
    return (1 if parsed else 0, parsed or "", nfc(path.name))


def collect(root: Path, query: str) -> tuple[list[tuple[str | None, Path]], list[Path], str]:
    """(정렬된 [날짜, 경로], 종목 폴더 목록, 매칭 방식)."""
    dirs, kind = find_stock_dirs(root, query)
    pdfs = [pdf for d in dirs for pdf in d.rglob("*.pdf")]
    pdfs.extend(find_aux_files(root, query))

    seen: set[Path] = set()
    items: list[tuple[str | None, Path]] = []
    for pdf in pdfs:
        resolved = pdf.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        items.append((parse_date(pdf.name), pdf))

    items.sort(key=_sort_key, reverse=True)
    return items, dirs, kind


def _display(path: Path) -> str:
    """홈 경로는 ~ 로 줄여 표시한다."""
    text = str(path)
    home = str(Path.home())
    return "~" + text[len(home) :] if text.startswith(home) else text


def render(query: str, root: Path, items, dirs, kind, limit: int | None, since: str | None) -> str:
    lines = [f"# 리포트 검색 — {query}", ""]
    lines.append(f"- 아카이브: `{_display(root)}`")
    lines.append(f"- 초성 폴더: `{chosung_dir(query)}`")

    if not root.is_dir():
        lines.append(f"- **아카이브 경로 없음** — `INVAGENT_REPORT_ARCHIVE`로 지정하라.")
        return "\n".join(lines) + "\n"

    if dirs:
        lines.append("- 매칭: " + kind + " → " + ", ".join(f"`{_display(d)}`" for d in dirs))
        if kind != "exact" and len(dirs) > 1:
            lines.append("- ⚠️ 후보 여러 개. 어느 종목인지 확정하지 않았다.")
    else:
        lines.append(f"- 매칭: 종목 폴더 없음 (파일명 보조 검색만)")

    total = len(items)
    if since:
        items = [(d, p) for d, p in items if d and d >= since]
    dated = items if limit is None else items[:limit]

    lines.append(f"- 전체 {total}건 / 조건 통과 {len(items)}건 / 선정 {len(dated)}건")
    lines.append("")

    if not dated:
        lines.append(f"검색 결과 없음. `{query}` 리포트가 아카이브에 없다.")
        return "\n".join(lines) + "\n"

    lines.append("| # | 날짜 | 파일 | 경로 |")
    lines.append("|---|---|---|---|")
    for idx, (parsed, path) in enumerate(dated, start=1):
        lines.append(f"| {idx} | {parsed or '—'} | {nfc(path.name)} | `{path}` |")

    remainder = len(items) - len(dated)
    if remainder > 0:
        lines.append("")
        lines.append(f"미선정 {remainder}건. 더 필요하면 `--limit N` 또는 `--all`.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="로컬 리포트 아카이브에서 종목 PDF 검색")
    parser.add_argument("stock", help="종목명 (예: 율촌화학)")
    parser.add_argument("--limit", type=int, default=5, help="최대 출력 건수 (기본 5)")
    parser.add_argument("--all", action="store_true", help="건수 제한 없이 전부 출력")
    parser.add_argument("--since", metavar="YYYY-MM-DD", help="이 날짜 이후 리포트만")
    args = parser.parse_args(argv)

    root = archive_root()
    items, dirs, kind = collect(root, args.stock)
    limit = None if args.all else max(args.limit, 0)
    sys.stdout.write(render(args.stock, root, items, dirs, kind, limit, args.since))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
