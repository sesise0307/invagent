"""Dated archive layout: `<root>/<yyyy>/<mm>/<yyyy-mm-dd>.md`.

Portfolio snapshots and daily briefings both accumulate one file a day, so they are
nested by year and month instead of piling up in one directory. Every reader and writer
goes through these helpers so the layout is defined in one place.
"""

from __future__ import annotations

import re
from pathlib import Path

_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-\d{2}$")
_YEAR_RE = re.compile(r"^\d{4}$")
_MONTH_RE = re.compile(r"^\d{2}$")


def dated_path(root: Path, day: str) -> Path:
    """`2026-10-04` → `<root>/2026/10/2026-10-04.md`."""
    match = _DATE_RE.match(day)
    if not match:
        raise ValueError(f"day must be YYYY-MM-DD: {day!r}")
    year, month = match.groups()
    return root / year / month / f"{day}.md"


def dated_files(root: Path) -> list[Path]:
    """Every `<yyyy>/<mm>/<yyyy-mm-dd>.md` under `root`, oldest first.

    A file whose date disagrees with its folders is misplaced and is skipped, as is
    anything else (backups, CSV dumps, theme files) that is not a dated briefing.
    """
    if not root.is_dir():
        return []
    found = [
        path
        for path in root.glob("*/*/*.md")
        if _DATE_RE.match(path.stem) and path.is_file() and dated_path(root, path.stem) == path
    ]
    return sorted(found, key=lambda path: path.stem)


def archive_root(path: Path) -> Path:
    """The archive root a dated file sits in (its parent when outside the layout)."""
    month, year = path.parent, path.parent.parent
    if _MONTH_RE.match(month.name) and _YEAR_RE.match(year.name):
        return year.parent
    return path.parent
