"""`invagent.core.files.atomic_write` — temp file in the same directory, then replace."""
from __future__ import annotations

import os

import pytest

from invagent.core.files import atomic_write


def test_atomic_write_writes_text_and_creates_parent_dirs(tmp_path):
    target = tmp_path / "a" / "b" / "state.json"

    atomic_write(target, "한글 내용\n")

    assert target.read_text(encoding="utf-8") == "한글 내용\n"
    assert os.listdir(target.parent) == ["state.json"]


def test_atomic_write_failure_keeps_original_and_leaves_no_temp(tmp_path, monkeypatch):
    target = tmp_path / "report.md"
    target.write_text("original", encoding="utf-8")

    def refuse(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(OSError):
        atomic_write(target, "new")

    assert target.read_text(encoding="utf-8") == "original"
    assert os.listdir(tmp_path) == ["report.md"]


def test_atomic_write_accepts_bytes(tmp_path):
    target = tmp_path / "body.bin"
    atomic_write(target, b"\x00\xff")
    assert target.read_bytes() == b"\x00\xff"
