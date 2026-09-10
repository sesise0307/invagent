"""링크 원문 옆 요약 파일을 raw export의 요약 대기 마커 자리에 반영한다.

daily-digest 1-5단계 3)에서 쓴다. 요약 서브에이전트는 병렬로 돌며 각자 원문 옆
`<이름>.summary.md`에만 쓰고 — 같은 raw 파일을 여럿이 동시에 고치면 서로 쓴 내용을
덮기 때문이다 — 이 스크립트가 마지막에 한 번에 raw를 고친다.

    uv run python .agents/skills/daily-digest/scripts/apply_link_summaries.py \\
        output/daily-digest/raw/<today>_raw.md

의존성은 표준 라이브러리와 `invagent` 패키지(마커 상수)뿐이다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from invagent.telegram.fetch import PENDING_LINK_SUMMARY_MARKER

FILE_LINE_PREFIX = "> 파일: "
PENDING_LINE = f"> {PENDING_LINK_SUMMARY_MARKER}"


def summary_path(body_path: Path) -> Path:
    """원문 `X.md` 옆의 요약 파일 `X.summary.md` 경로."""
    return body_path.with_name(body_path.stem + ".summary.md")


def apply_summaries(raw_text: str) -> tuple[str, int, list[Path]]:
    """`> 파일: X` 바로 다음 줄의 요약 대기 마커를 X의 요약 파일 내용으로 바꾼다.

    바꾼 텍스트, 반영 건수, 아직 요약 파일이 없는 원문 경로 목록을 돌려준다.
    """
    lines = raw_text.split("\n")
    out: list[str] = []
    applied = 0
    pending: list[Path] = []
    for index, line in enumerate(lines):
        previous = lines[index - 1] if index > 0 else ""
        if line == PENDING_LINE and previous.startswith(FILE_LINE_PREFIX):
            body = Path(previous[len(FILE_LINE_PREFIX):])
            sidecar = summary_path(body)
            # 아직 요약되지 않은 링크는 마커를 남겨 두어, 재위임 후 다시 돌리면 이어서 반영된다.
            if sidecar.exists():
                summary = sidecar.read_text(encoding="utf-8").strip("\n")
                out.extend(f"> {summary_line}" for summary_line in summary.split("\n"))
                applied += 1
                continue
            pending.append(body)
        out.append(line)
    return "\n".join(out), applied, pending


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="링크 요약 파일을 raw export의 요약 대기 마커 자리에 반영한다."
    )
    parser.add_argument("raw", type=Path, help="output/daily-digest/raw/<날짜>_raw.md")
    args = parser.parse_args(argv)

    text, applied, pending = apply_summaries(args.raw.read_text(encoding="utf-8"))
    args.raw.write_text(text, encoding="utf-8")
    # 요약 내용은 찍지 않는다. 브리핑을 쓰는 에이전트는 raw를 읽을 때 요약을 처음 만난다.
    print(f"반영 {applied}건 · 대기 {len(pending)}건")
    for body in pending:
        print(f"대기: {body}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
