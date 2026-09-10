#!/usr/bin/env python3
"""`output/reports/`에서 같은 종목의 과거 분석 보고서를 찾아 승계 대상을 판정한다.

보고서는 세 갈래로 나뉜다.

    종목/<초성>/<종목명>_<YYYY-MM-DD>.md   개별 종목 분석 → 승계 대상
    산업/<YYYY-MM-DD>_<주제>_….md          섹터·테마 비교 → 참고
    기타/<YYYY-MM-DD>_<주제>_….md          전략·스크리닝  → 참고

    종목/ㅇ/율촌화학_2026-08-14.md                        → 승계
    산업/2026-08-11_기판검사장비_기가비스_인텍플러스_비교.md → 참고
    기타/2026-08_투자전략.md                              → 무시 (일자 없음)

산업·기타 보고서는 다른 종목 내용도 담고 있으므로 승계하지 않고 원본을 유지한다.
초성 폴더 규칙은 로컬 PDF 아카이브와 같다 — `find_reports.chosung_dir()`을 재사용한다.

기본 경로는 저장소 루트의 `output/` 이며 INVAGENT_OUTPUT_DIR 로 덮어쓸 수 있다.
표준 라이브러리만 사용한다.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from invagent.datafeed.env import output_dir
from invagent.datafeed.text import nfc, norm as _key

sys.path.insert(0, str(Path(__file__).resolve().parent))

from find_reports import chosung_dir  # noqa: E402  (같은 스크립트 폴더)

REPORTS_SUBDIR = "reports"
STOCK_DIR = "종목"
SECTOR_DIR = "산업"
MISC_DIR = "기타"

# 종목 보고서 — 날짜가 뒤에 붙는다.
_STOCK_RE = re.compile(r"^(.+)_(\d{4}-\d{2}-\d{2})$")
# 산업·기타 보고서 — 날짜가 앞에 붙는 기존 관례.
_DATED_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(.+)$")





@dataclass
class Report:
    """과거 보고서 하나."""

    path: Path
    date: str
    tokens: list[str]
    stock_report: bool = False

    @property
    def size_kb(self) -> int:
        return max(1, round(self.path.stat().st_size / 1024))


@dataclass
class Lookup:
    stock: str
    target: Path
    inherit: Report | None = None
    previous: list[Report] = field(default_factory=list)
    related: list[Report] = field(default_factory=list)

    @property
    def same_path(self) -> bool:
        """오늘 이미 같은 경로가 승계 대상인지 판정한다."""
        return self.inherit is not None and self.inherit.path == self.target

    @property
    def since(self) -> str | None:
        return self.inherit.date if self.inherit else None


def parse_dated(path: Path) -> Report | None:
    """`<YYYY-MM-DD>_<주제>_….md` — 산업·기타 보고서. 일자가 없으면 대상이 아니다."""
    match = _DATED_RE.match(nfc(path.stem))
    if not match:
        return None
    return Report(path=path, date=match.group(1), tokens=match.group(2).split("_"))


def parse_stock(path: Path) -> Report | None:
    """`<종목명>_<YYYY-MM-DD>.md` — 종목 보고서. 구 파일명은 옛 관례로 한 번 더 시도한다."""
    match = _STOCK_RE.match(nfc(path.stem))
    if match:
        return Report(
            path=path, date=match.group(2), tokens=[match.group(1)], stock_report=True
        )
    legacy = parse_dated(path)
    if legacy is not None:
        legacy.stock_report = True
    return legacy


def target_path(reports_dir: Path, stock: str, today: str) -> Path:
    """새 보고서를 쓸 자리. 초성 폴더는 PDF 아카이브와 같은 규칙."""
    return reports_dir / STOCK_DIR / chosung_dir(stock) / f"{nfc(stock)}_{today}.md"


def scan(reports_dir: Path) -> list[Report]:
    """세 분류 폴더의 마크다운을 본다.

    `종목/`은 초성 하위까지 훑고, `산업/`·`기타/`와 루트(이관 누락분)는 바로 아래만 본다.
    루트에는 `<YYYY-MM-DD>/` 디렉터리로 내려받은 PDF가 쌓이므로 그 하위는 훑지 않는다.
    """
    if not reports_dir.is_dir():
        return []

    found: list[Report | None] = []
    found += [parse_stock(path) for path in sorted((reports_dir / STOCK_DIR).glob("**/*.md"))]
    for category in (SECTOR_DIR, MISC_DIR):
        found += [parse_dated(path) for path in sorted((reports_dir / category).glob("*.md"))]
    found += [parse_dated(path) for path in sorted(reports_dir.glob("*.md"))]
    return [report for report in found if report is not None]


def lookup(reports_dir: Path, stock: str, today: str) -> Lookup:
    """승계 대상 · 이전 개정 · 참고 보고서를 분류한다."""
    result = Lookup(stock=nfc(stock), target=target_path(reports_dir, stock, today))

    key = _key(stock)
    owned: list[Report] = []
    for report in scan(reports_dir):
        if not report.tokens:
            continue
        if report.stock_report and _key(report.tokens[0]) == key:
            owned.append(report)
        elif not report.stock_report and any(key == _key(token) for token in report.tokens):
            result.related.append(report)

    owned.sort(key=lambda r: (r.date, nfc(r.path.name)), reverse=True)
    result.related.sort(key=lambda r: (r.date, nfc(r.path.name)), reverse=True)
    if owned:
        result.inherit, result.previous = owned[0], owned[1:]
    return result


def _rel(path: Path, output_root: Path) -> str:
    try:
        return str(path.relative_to(output_root.parent))
    except ValueError:
        return str(path)


def render(result: Lookup, output_root: Path) -> str:
    lines = [f"# 과거 보고서 — {result.stock}", ""]

    if result.inherit is None:
        lines.append("- 승계 대상: 없음 (신규 작성)")
        lines.append(f"- 목표 경로: `{_rel(result.target, output_root)}`")
    else:
        inherit = result.inherit
        lines.append(
            f"- 승계 대상: `{_rel(inherit.path, output_root)}` ({inherit.date}, {inherit.size_kb}KB)"
        )
        lines.append(f"- 목표 경로: `{_rel(result.target, output_root)}`")
        if result.same_path:
            lines.append("- 같은 경로 여부: **yes** → 같은 날 재실행. 본문은 현재 상태로 갱신하고 변경은 §12에 한 줄로 남겨라.")
        else:
            lines.append("- 같은 경로 여부: no → 승계 파일명을 목표 경로로 먼저 변경한 뒤 같은 파일에 증분을 추가하라. 복사본을 만들지 마라.")
        lines.append(f"- 증분 기준일(`--since`): {inherit.date}")

    if result.previous:
        names = ", ".join(f"`{nfc(r.path.name)}`" for r in result.previous)
        lines.append(f"- 과거 중복 후보 {len(result.previous)}건: {names}")
    if result.related:
        names = ", ".join(f"`{nfc(r.path.name)}`" for r in result.related)
        lines.append(f"- 참고(비교·섹터) {len(result.related)}건: {names}")

    if result.inherit is not None:
        lines += [
            "",
            "승계본 전문을 먼저 읽어라. 이후 단계는 증분 기준일 이후만 새로 수집한다.",
            "승계본과 목표 경로가 다르면 수집·작성 전에 파일명을 목표 경로로 변경한다.",
            "승계본의 `## 12. 개정 이력`을 반드시 읽어라 — 과거 판단·정정 이력이 거기에만 있다.",
            "본문은 현재 상태만 유지한다. 옛 날짜 블록·보존 블록은 §12에 한 줄로 옮긴 뒤 본문에서 뺀다.",
            "변경은 `## 12. 개정 이력`에 `- YYYY-MM-DD: 구분 — 요약` 한 줄. 정정·판단변경은 근거를 붙인다.",
        ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="같은 종목의 과거 분석 보고서 탐색")
    parser.add_argument("stock", help="종목명 (예: 율촌화학)")
    parser.add_argument("--today", metavar="YYYY-MM-DD", help="목표 파일명에 쓸 날짜 (기본 오늘)")
    args = parser.parse_args(argv)

    today = args.today or date.today().isoformat()
    reports_dir = output_dir() / REPORTS_SUBDIR
    result = lookup(reports_dir, args.stock, today)
    sys.stdout.write(render(result, output_dir()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
