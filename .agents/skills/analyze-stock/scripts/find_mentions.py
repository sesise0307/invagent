#!/usr/bin/env python3
"""텔레그램 아카이브에서 종목 언급 위치를 찾아 로케이터 표로 출력한다.

본문을 뿌리지 않는다. `경로:줄번호`만 주고, 판단이 걸린 항목은 에이전트가 원본을
직접 읽는다. 아카이브의 인덱스·테마 파일은 한 줄이 4KB까지 가므로 줄 단위 grep은
크기가 폭발하고, `daily-digest` 스킬은 이 파일들을 잘라 읽는 것을 금지한다.

스캔 대상:
    output/telegram-daily/monthly_context.md        누적 인덱스 (현재 유효 판정)
    output/telegram-daily/themes/<slug>.md          테마 전문 (일자 서브불릿)
    output/telegram-daily/themes/archive/**.md      롤오프분
    output/telegram-daily/<YYYY-MM>/<YYYY-MM-DD>.md 일일 브리핑

기본 경로는 저장소 루트의 `output/` 이며 INVAGENT_OUTPUT_DIR 로 덮어쓸 수 있다.
표준 라이브러리만 사용한다.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from invagent.datafeed.env import output_dir
from invagent.datafeed.text import nfc, norm as _key

INDEX_NAME = "monthly_context.md"
THEMES_DIR = "themes"

# 현행 내용의 중복본. 포함하면 결과가 부풀고 이미 정리된 판정이 되살아난다.
EXCLUDED_NAMES = {
    "monthly_context.md.bak",
    "backup.md",
    "pruned-2026-08-09-정리전-인덱스-전체.md",
}

MARKERS = ("⭐", "⚠️", "⛔")
# 한글 1자 ≈ 3바이트. 표 한 행이 커지지 않게 글자 수로 제한한다.
EXCERPT_LIMIT = 150

_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_RECENT_RE = re.compile(r"최근\(((?:\d{4}-)?\d{2}-\d{2})\)")
_POINTER_RE = re.compile(r"\[\[themes/([^\]|]+)")
_BOLD_HEAD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_MONTH_DIR_RE = re.compile(r"^\d{4}-\d{2}$")
_DAILY_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")





@dataclass
class Record:
    """아카이브의 의미 단위 하나. 본문은 매칭에만 쓰고 출력하지 않는다."""

    source: str  # index / theme / daily
    path: Path
    line: int
    head: str
    body: str = ""
    date: str | None = None
    label: str = ""
    marker: str = ""
    pointer: str = ""
    match: str = ""  # head / body

    @property
    def head_key(self) -> str:
        return _key(self.head)

    @property
    def body_key(self) -> str:
        return _key(self.body)


def _marker_of(text: str) -> str:
    for mark in MARKERS:
        if mark in text:
            return mark
    return ""


def _excerpt(text: str, limit: int = EXCERPT_LIMIT) -> str:
    """표 한 칸에 넣을 발췌. 줄바꿈·파이프 제거."""
    flat = " ".join(nfc(text).split()).replace("|", "\\|")
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _line_fallback(path: Path, source: str, label: str, lines: list[str]) -> list[Record]:
    """레코드 구조가 안 잡히는 파일용 폴백 — 줄 단위로 훑는다."""
    return [
        Record(source=source, path=path, line=idx, head=line.strip(), label=label)
        for idx, line in enumerate(lines, start=1)
        if line.strip()
    ]


def parse_daily(path: Path) -> list[Record]:
    """일일 브리핑: `- **종목**, 헤드라인` + 이어지는 들여쓰기 블록이 레코드 하나."""
    match = _DAILY_FILE_RE.match(nfc(path.name))
    day = match.group(1) if match else None
    lines = path.read_text(encoding="utf-8").splitlines()

    records: list[Record] = []
    section = ""
    current: Record | None = None
    body: list[str] = []

    def close() -> None:
        nonlocal current, body
        if current is not None:
            current.body = "\n".join(body)
            records.append(current)
        current, body = None, []

    for idx, raw in enumerate(lines, start=1):
        line = nfc(raw)
        stripped = line.strip()
        if stripped.startswith("#"):
            close()
            section = stripped.lstrip("#").strip()
            continue
        if line.startswith("- "):
            close()
            bold = _BOLD_HEAD_RE.search(line)
            head = bold.group(1) if bold else stripped[2:]
            current = Record(
                source="daily",
                path=path,
                line=idx,
                head=head,
                date=day,
                label=section,
                marker=_marker_of(stripped),
            )
            # 볼드 뒤에 붙는 나머지 문장도 검색 대상이다. 본문에 함께 담는다.
            body = [stripped[2:]]
            continue
        if current is not None and (line[:1].isspace() or not stripped):
            body.append(stripped)
            continue
        close()

    close()
    if not records:
        return _line_fallback(path, "daily", section, lines)
    for record in records:
        record.date = day
    return records


def parse_theme(path: Path) -> list[Record]:
    """테마 파일: `  - YYYY-MM-DD: …` 서브불릿 하나가 레코드 하나."""
    slug = nfc(path.stem)
    lines = path.read_text(encoding="utf-8").splitlines()

    records: list[Record] = []
    for idx, raw in enumerate(lines, start=1):
        line = nfc(raw)
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        text = stripped[2:]
        found = _DATE_RE.match(text) or _DATE_RE.search(text[:40])
        records.append(
            Record(
                source="theme",
                path=path,
                line=idx,
                head=text,
                date=found.group(0) if found else None,
                label=slug,
                marker=_marker_of(text),
            )
        )

    return records or _line_fallback(path, "theme", slug, lines)


def parse_index(path: Path) -> list[Record]:
    """누적 인덱스: 항목 1줄이 레코드 하나 = 지금 유효한 판정."""
    lines = path.read_text(encoding="utf-8").splitlines()

    records: list[Record] = []
    section = ""
    for idx, raw in enumerate(lines, start=1):
        line = nfc(raw)
        stripped = line.strip()
        if stripped.startswith("#"):
            section = stripped.lstrip("#").strip()
            continue
        if not stripped.startswith("- "):
            continue
        text = stripped[2:]
        pointer = _POINTER_RE.search(text)
        recent = _RECENT_RE.search(text)
        date = None
        if recent:
            token = recent.group(1)
            date = token if len(token) == 10 else None
        elif full := _DATE_RE.search(text[:60]):
            date = full.group(0)
        records.append(
            Record(
                source="index",
                path=path,
                line=idx,
                head=text,
                date=date,
                label=section,
                marker=_marker_of(text),
                pointer=pointer.group(1) if pointer else "",
            )
        )

    return records or _line_fallback(path, "index", section, lines)


def scan(archive: Path) -> list[Record]:
    """아카이브 전체를 레코드로 파싱한다. 캐시 없음."""
    if not archive.is_dir():
        return []

    records: list[Record] = []
    index_path = archive / INDEX_NAME
    if index_path.is_file():
        records.extend(parse_index(index_path))

    themes = archive / THEMES_DIR
    if themes.is_dir():
        for path in sorted(themes.rglob("*.md")):
            if nfc(path.name) in EXCLUDED_NAMES:
                continue
            records.extend(parse_theme(path))

    for month in sorted(archive.iterdir()):
        if not month.is_dir() or not _MONTH_DIR_RE.match(nfc(month.name)):
            continue
        for path in sorted(month.glob("*.md")):
            if nfc(path.name) in EXCLUDED_NAMES:
                continue
            records.extend(parse_daily(path))

    return records


def match_records(records: list[Record], terms: list[str]) -> list[Record]:
    """헤드 매치와 본문 매치를 구분해 표시한다."""
    keys = [_key(term) for term in terms if term.strip()]
    hits: list[Record] = []
    for record in records:
        head_key, body_key = record.head_key, record.body_key
        if any(key in head_key for key in keys):
            record.match = "head"
        elif any(key in body_key for key in keys):
            record.match = "body"
        else:
            continue
        hits.append(record)
    return hits


def _sort_hits(hits: list[Record]) -> list[Record]:
    """헤드 매치 우선, 그다음 최신순. 날짜 없는 건 뒤로."""
    return sorted(
        hits,
        key=lambda r: (r.match == "head", r.date is not None, r.date or "", -r.line),
        reverse=True,
    )


@dataclass
class Result:
    terms: list[str]
    index_hits: list[Record] = field(default_factory=list)
    theme_hits: list[Record] = field(default_factory=list)
    daily_hits: list[Record] = field(default_factory=list)

    @property
    def all_hits(self) -> list[Record]:
        return self.index_hits + self.theme_hits + self.daily_hits


def collect(archive: Path, terms: list[str], since: str | None = None) -> Result:
    hits = match_records(scan(archive), terms)
    if since:
        hits = [h for h in hits if h.date and h.date >= since]
    return Result(
        terms=terms,
        index_hits=_sort_hits([h for h in hits if h.source == "index"]),
        theme_hits=_sort_hits([h for h in hits if h.source == "theme"]),
        daily_hits=_sort_hits([h for h in hits if h.source == "daily"]),
    )


def _rel(path: Path, archive: Path, line: int) -> str:
    try:
        shown = path.relative_to(archive.parent)
    except ValueError:
        shown = path
    return f"`{shown}:{line}`"


def _summary(result: Result, archive: Path) -> list[str]:
    hits = result.all_hits
    dates = sorted(h.date for h in hits if h.date)
    files = {h.path for h in hits}
    heads = sum(1 for h in hits if h.match == "head")
    terms = " / ".join(result.terms)

    lines = ["## 요약", ""]
    if not hits:
        lines.append(f"- 검색어: {terms} — **아카이브 언급 없음** (`{archive.name}/`)")
        return lines
    lines.append(f"- 검색어: {terms}")
    lines.append(f"- 최초 {dates[0] if dates else '—'} · 최근 {dates[-1] if dates else '—'}")
    lines.append(f"- 헤드 매치 {heads}건 / 본문 매치 {len(hits) - heads}건 / 파일 {len(files)}개")
    lines.append(
        f"- 소스별: 인덱스 {len(result.index_hits)} · 테마 {len(result.theme_hits)} · 일일 {len(result.daily_hits)}"
    )
    return lines


def _unshown(hits: list[Record], shown: int) -> list[str]:
    rest = hits[shown:]
    if not rest:
        return []
    buckets: dict[str, int] = {}
    for record in rest:
        month = record.date[:7] if record.date else "날짜없음"
        buckets[month] = buckets.get(month, 0) + 1
    parts = " · ".join(f"{month} {count}건" for month, count in sorted(buckets.items(), reverse=True))
    return ["", f"미표시 {len(rest)}건 — {parts}. 더 필요하면 `--top N` 또는 `--all`."]


def render(result: Result, archive: Path, top: int | None, count_only: bool) -> str:
    lines = _summary(result, archive)
    if count_only or not result.all_hits:
        return "\n".join(lines) + "\n"

    if result.index_hits:
        lines += [
            "",
            "## 인덱스 현재 판정",
            "",
            "> 항목 1줄 = 지금 유효한 판정. 항목 상한이 400자라 전문 그대로 싣는다.",
            "",
            "| 마커 | 항목 | 테마 | 위치 |",
            "|---|---|---|---|",
        ]
        for record in result.index_hits:
            pointer = f"`{record.pointer}`" if record.pointer else "—"
            body = _excerpt(record.head, 400)
            lines.append(f"| {record.marker or '—'} | {body} | {pointer} | {_rel(record.path, archive, record.line)} |")

    if result.theme_hits:
        shown = result.theme_hits if top is None else result.theme_hits[:top]
        lines += [
            "",
            "## 테마 타임라인",
            "",
            "| 날짜 | 테마 | 발췌 | 매치 | 위치 |",
            "|---|---|---|---|---|",
        ]
        for record in shown:
            lines.append(
                f"| {record.date or '—'} | {record.label} | {_excerpt(record.head)} "
                f"| {record.match} | {_rel(record.path, archive, record.line)} |"
            )
        lines += _unshown(result.theme_hits, len(shown))

    if result.daily_hits:
        shown = result.daily_hits if top is None else result.daily_hits[:top]
        lines += [
            "",
            "## 일일 브리핑",
            "",
            "| 날짜 | 섹션 | 발췌 | 매치 | 위치 |",
            "|---|---|---|---|---|",
        ]
        for record in shown:
            lines.append(
                f"| {record.date or '—'} | {_excerpt(record.label, 30)} | {_excerpt(record.head)} "
                f"| {record.match} | {_rel(record.path, archive, record.line)} |"
            )
        lines += _unshown(result.daily_hits, len(shown))

    lines += [
        "",
        "---",
        "",
        "발췌는 로케이터다. 결론을 좌우하는 근거는 위 경로의 원본을 직접 읽어라.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="텔레그램 아카이브에서 종목 언급 위치 검색")
    parser.add_argument("stock", help="종목명 (예: 이수페타시스)")
    parser.add_argument("--alias", action="append", default=[], help="축약형·별칭 (반복 가능)")
    parser.add_argument("--top", type=int, default=20, help="표별 최대 행 수 (기본 20)")
    parser.add_argument("--all", action="store_true", help="행 수 제한 없이 전부 출력")
    parser.add_argument("--since", metavar="YYYY-MM-DD", help="이 날짜 이후 언급만")
    parser.add_argument("--count-only", action="store_true", help="요약만 출력")
    args = parser.parse_args(argv)

    archive = output_dir() / "telegram-daily"
    if not archive.is_dir():
        sys.stdout.write(
            f"# 아카이브 검색 — {args.stock}\n\n"
            f"- 아카이브 경로 없음: `{archive}`. `INVAGENT_OUTPUT_DIR`로 지정하라.\n"
        )
        return 0

    terms = [args.stock, *args.alias]
    result = collect(archive, terms, args.since)
    top = None if args.all else max(args.top, 0)
    sys.stdout.write(f"# 아카이브 검색 — {args.stock}\n\n")
    sys.stdout.write(render(result, archive, top, args.count_only))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
