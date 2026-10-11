#!/usr/bin/env python3
"""Write an atomic, auditable manifest for one analyze-stock run.

The script owns its path: `output/reports/run-manifests/<yyyy>/<mm>/<yyyy-mm-dd>_<종목명>.json`,
nested by `core.archive.dated_path`. The same run (same `--run-id`) rewrites its own file;
a different run on the same day for the same stock goes to `<yyyy-mm-dd>_<종목명>_<run-id>.json`
so neither run's record is lost.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from invagent.core.archive import dated_path
from invagent.core.files import atomic_write
from invagent.datafeed.env import output_dir
from invagent.datafeed.text import nfc

MANIFEST_SUBDIR = Path("reports") / "run-manifests"
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_UNSAFE_STOCK_CHARS = ("/", "\\", "\x00")


def stock_name_error(stock: str) -> str | None:
    """Why `stock` cannot be a filename component, or None when it is safe.

    The name lands in the manifest filename, so a path separator, NUL, `.`/`..` or an
    empty name could escape or collide with the manifest folder.
    """
    if stock.strip() in ("", ".", ".."):
        return f"종목명이 비었거나 경로 성분이다: {stock!r}"
    if any(char in stock for char in _UNSAFE_STOCK_CHARS):
        return f"종목명에 경로 구분자나 NUL을 쓸 수 없다: {stock!r}"
    return None


def manifest_path(root: Path, stock: str, day: str, run_id: str) -> Path:
    """`<root>/<yyyy>/<mm>/<day>_<stock>.json`, or `…_<run_id>.json` when another run holds it."""
    folder = dated_path(root, day).parent
    base = folder / f"{day}_{nfc(stock)}.json"
    if not base.exists() or _run_id_of(base) == run_id:
        return base
    return folder / f"{day}_{nfc(stock)}_{run_id}.json"


def _run_id_of(path: Path) -> str | None:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("run_id")
    except (OSError, ValueError, AttributeError):
        return None


def write_manifest(path: Path, run_id: str, steps: list[dict]) -> None:
    payload = {
        "schema_version": 1,
        "run_id": run_id,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "steps": steps,
    }
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="analyze-stock 실행 매니페스트 기록")
    p.add_argument("stock", help="종목명 (예: 율촌화학)")
    p.add_argument("--run-id", required=True, help="실행 식별자 (영숫자 . _ -)")
    p.add_argument("--date", metavar="YYYY-MM-DD", help="실행일 (기본 오늘)")
    p.add_argument("--step", action="append", default=[], help="name=status[:elapsed_ms]")
    a = p.parse_args(argv)
    if not _RUN_ID_RE.match(a.run_id):
        p.error(f"--run-id 는 영숫자와 . _ - 만 쓴다: {a.run_id!r}")
    if (problem := stock_name_error(a.stock)) is not None:
        p.error(problem)
    day = a.date or date.today().isoformat()
    steps = []
    for raw in a.step:
        name, _, rest = raw.partition("=")
        status, _, elapsed = rest.partition(":")
        steps.append({"name": name, "status": status, "elapsed_ms": int(elapsed or 0)})
    try:
        path = manifest_path(output_dir() / MANIFEST_SUBDIR, a.stock, day, a.run_id)
    except ValueError as error:
        p.error(str(error))
    write_manifest(path, a.run_id, steps)
    sys.stdout.write(f"매니페스트 기록: {path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
