#!/usr/bin/env python3
"""Merge one weekly review block into a Notion page body without losing the body.

`replace_content` on the Notion page overwrites the whole body, so the agent must
hand it a complete merged body. This builds that body locally and refuses to emit
one that dropped a heading.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HEADING = re.compile(r"^##\s+(.*?)\s*$", re.MULTILINE)


def headings(body: str) -> list[str]:
    """Every `## ` heading, in order, with Notion's `\\~` escape normalised away."""
    return [h.replace("\\~", "~") for h in HEADING.findall(body)]


def assert_no_heading_loss(before: str, after: str) -> None:
    """Raise when merging dropped a heading the source body had."""
    had, kept = headings(before), headings(after)
    lost = [h for h in had if h not in kept]
    if lost:
        raise ValueError(
            f"heading loss: {lost} present in source but missing from merged body "
            f"({len(had)} -> {len(kept)})"
        )


WEEKLY_BULLET = "- 주간 투자 반성"


def _weekend_heading_index(lines: list[str], weekend: str) -> int:
    """Row of the `## {weekend}(주말)` heading, or -1. Matches Notion's `\\~` escape."""
    wanted = f"{weekend}(주말)".replace("\\~", "~")
    for i, line in enumerate(lines):
        match = HEADING.match(line)
        if match and match.group(1).replace("\\~", "~").startswith(wanted):
            return i
    return -1


def _block_end(lines: list[str], start: int) -> int:
    """Row after the weekly bullet's own indented descendants."""
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line.strip() and not line.startswith((" ", "\t")):
            break
        end += 1
    return end


def upsert(existing: str, proposed: str, weekend: str) -> str:
    """Replace (or insert) the weekly block under its weekend heading."""
    lines = existing.split("\n")
    head = _weekend_heading_index(lines, weekend)
    if head < 0:
        raise ValueError(f"weekend heading not found: ## {weekend}(주말)")

    block = proposed.strip("\n").split("\n")
    start = head + 1
    while start < len(lines) and not lines[start].strip():
        start += 1

    if start < len(lines) and lines[start].strip() == WEEKLY_BULLET:
        merged = lines[:start] + block + lines[_block_end(lines, start):]
    else:
        merged = lines[: head + 1] + block + lines[head + 1:]

    out = "\n".join(merged)
    assert_no_heading_loss(existing, out)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--existing", required=True, type=Path, help="백업해 둔 현재 페이지 본문 전체")
    parser.add_argument("--proposed", required=True, type=Path, help="이번 주 반성 블록")
    parser.add_argument("--weekend", required=True, help='주말 헤딩, 예: "19~20"')
    parser.add_argument("--prepared-out", required=True, type=Path, help="병합된 전체 본문")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        existing = args.existing.read_text(encoding="utf-8")
        merged = upsert(existing, args.proposed.read_text(encoding="utf-8"), args.weekend)
    except (OSError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 2

    args.prepared_out.parent.mkdir(parents=True, exist_ok=True)
    args.prepared_out.write_text(merged, encoding="utf-8")
    report = {
        "ok": True,
        "weekend": args.weekend,
        "replaced": WEEKLY_BULLET in existing,
        "headings": len(headings(merged)),
    }
    print(json.dumps(report, ensure_ascii=False) if args.json else str(args.prepared_out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
