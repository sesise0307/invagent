#!/usr/bin/env python3
"""`output/reports/`에서 같은 종목의 과거 분석 보고서를 찾아 승계 대상을 판정한다.

파일명 관례는 `<YYYY-MM-DD>_<주제>_<유형>.md` 다. 날짜 다음 첫 토큰이 종목명인
파일만 승계 대상으로 본다. 종목명이 중간에 끼어 있으면 비교·섹터 보고서라
다른 종목 내용도 담겨 있으므로 참고로만 분류한다.

    2026-08-14_율촌화학_반기보고서_분석.md               → 승계
    2026-08-11_기판검사장비_기가비스_인텍플러스_비교.md  → 참고
    2026-08_투자전략.md                                  → 무시 (일자 없음)

기본 경로는 저장소 루트의 `output/` 이며 INVAGENT_OUTPUT_DIR 로 덮어쓸 수 있다.
표준 라이브러리만 사용한다.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
REPORTS_SUBDIR = "reports"
REPORT_SUFFIX = "종목분석"

_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(.+)$")


def nfc(text: str) -> str:
    """macOS 파일명은 NFD로 저장된다. 비교 전 NFC로 정규화한다."""
    return unicodedata.normalize("NFC", text)


def _key(text: str) -> str:
    """비교용 키 — NFC + 소문자 + 공백/구두점 제거."""
    return re.sub(r"[\s._·\-()\[\]{},;:'\"]+", "", nfc(text).lower())


def output_dir() -> Path:
    """보고서가 담긴 output 디렉터리. 환경변수 우선."""
    override = os.environ.get("INVAGENT_OUTPUT_DIR")
    return Path(override).expanduser() if override else REPO_ROOT / "output"


@dataclass
class Report:
    """과거 보고서 하나."""

    path: Path
    date: str
    tokens: list[str]

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
        """오늘 이미 같은 경로가 승계 대상이면 구 파일을 지우면 안 된다."""
        return self.inherit is not None and self.inherit.path == self.target

    @property
    def since(self) -> str | None:
        return self.inherit.date if self.inherit else None


def parse_report(path: Path) -> Report | None:
    """파일명에서 작성일과 토큰을 뽑는다. 일자가 없으면 대상이 아니다."""
    match = _NAME_RE.match(nfc(path.stem))
    if not match:
        return None
    return Report(path=path, date=match.group(1), tokens=match.group(2).split("_"))


def scan(reports_dir: Path) -> list[Report]:
    """`output/reports/` 바로 아래 마크다운만 본다.

    같은 부모에 `<YYYY-MM-DD>/` 디렉터리로 내려받은 PDF가 쌓이므로 하위는 훑지 않는다.
    """
    if not reports_dir.is_dir():
        return []
    found = [parse_report(path) for path in sorted(reports_dir.glob("*.md"))]
    return [report for report in found if report is not None]


def lookup(reports_dir: Path, stock: str, today: str) -> Lookup:
    """승계 대상 · 이전 개정 · 참고 보고서를 분류한다."""
    target = reports_dir / f"{today}_{nfc(stock)}_{REPORT_SUFFIX}.md"
    result = Lookup(stock=nfc(stock), target=target)

    key = _key(stock)
    owned: list[Report] = []
    for report in scan(reports_dir):
        if not report.tokens:
            continue
        if _key(report.tokens[0]) == key:
            owned.append(report)
        elif any(key == _key(token) for token in report.tokens[1:]):
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
            lines.append("- 같은 경로 여부: **yes** → 같은 날 재실행. 구 파일을 삭제하지 마라.")
        else:
            lines.append("- 같은 경로 여부: no → 새 경로로 전문을 쓴 뒤 구 파일을 삭제하라.")
        lines.append(f"- 증분 기준일(`--since`): {inherit.date}")

    if result.previous:
        names = ", ".join(f"`{nfc(r.path.name)}`" for r in result.previous)
        lines.append(f"- 이전 개정 {len(result.previous)}건 (참고만): {names}")
    if result.related:
        names = ", ".join(f"`{nfc(r.path.name)}`" for r in result.related)
        lines.append(f"- 참고(비교·섹터) {len(result.related)}건: {names}")

    if result.inherit is not None:
        lines += [
            "",
            "승계본 전문을 먼저 읽어라. 이후 단계는 증분 기준일 이후만 새로 수집한다.",
            "수치·기준이 어긋나면 고치고 `## 12. 개정 이력`에 근거와 함께 남긴다.",
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
