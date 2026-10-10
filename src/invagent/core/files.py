"""Atomic file writes shared by the package and the skill scripts."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write(path: Path, data: str | bytes) -> None:
    """Write `data` to `path` through a temp file in the same directory, then `os.replace`.

    Text is encoded as UTF-8. Parent directories are created. Readers never see a
    half-written file, and on failure the original file is untouched and the temp file
    is removed before the error propagates.
    """
    path = Path(path)
    payload = data.encode("utf-8") if isinstance(data, str) else data
    path.parent.mkdir(parents=True, exist_ok=True)
    # 같은 경로를 여러 프로세스가 써도 임시 파일 이름이 겹치지 않는다.
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
