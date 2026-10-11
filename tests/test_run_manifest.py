"""analyze-stock `run_manifest.py` — 매니페스트가 스스로 경로를 정한다."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import load_script


def test_manifest_path_is_derived_from_date_and_stock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_script("analyze-stock", "run_manifest")
    monkeypatch.setenv("INVAGENT_OUTPUT_DIR", str(tmp_path))

    assert module.main(["율촌화학", "--run-id", "r1", "--date", "2026-10-10", "--step", "6=ok:1200", "--step", "5=skipped"]) == 0

    path = tmp_path / "reports" / "run-manifests" / "2026" / "10" / "2026-10-10_율촌화학.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "r1"
    assert payload["steps"] == [
        {"name": "6", "status": "ok", "elapsed_ms": 1200},
        {"name": "5", "status": "skipped", "elapsed_ms": 0},
    ]


def test_same_run_id_rewrites_its_own_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_script("analyze-stock", "run_manifest")
    monkeypatch.setenv("INVAGENT_OUTPUT_DIR", str(tmp_path))
    base = ["율촌화학", "--run-id", "r1", "--date", "2026-10-10"]

    module.main(base + ["--step", "6=ok"])
    module.main(base + ["--step", "6=ok", "--step", "7=ok"])

    folder = tmp_path / "reports" / "run-manifests" / "2026" / "10"
    assert [p.name for p in folder.iterdir()] == ["2026-10-10_율촌화학.json"]
    payload = json.loads((folder / "2026-10-10_율촌화학.json").read_text(encoding="utf-8"))
    assert len(payload["steps"]) == 2


def test_another_run_on_the_same_day_does_not_clobber(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = load_script("analyze-stock", "run_manifest")
    monkeypatch.setenv("INVAGENT_OUTPUT_DIR", str(tmp_path))

    module.main(["율촌화학", "--run-id", "r1", "--date", "2026-10-10", "--step", "6=ok"])
    capsys.readouterr()
    assert module.main(["율촌화학", "--run-id", "r2", "--date", "2026-10-10", "--step", "6=failed"]) == 0

    folder = tmp_path / "reports" / "run-manifests" / "2026" / "10"
    first = json.loads((folder / "2026-10-10_율촌화학.json").read_text(encoding="utf-8"))
    second = json.loads((folder / "2026-10-10_율촌화학_r2.json").read_text(encoding="utf-8"))
    assert first["run_id"] == "r1"
    assert second["run_id"] == "r2"
    assert "2026-10-10_율촌화학_r2.json" in capsys.readouterr().out


def test_run_id_cannot_escape_the_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_script("analyze-stock", "run_manifest")
    monkeypatch.setenv("INVAGENT_OUTPUT_DIR", str(tmp_path))

    with pytest.raises(SystemExit):
        module.main(["율촌화학", "--run-id", "../x", "--step", "6=ok"])


@pytest.mark.parametrize("stock", ["../x", "a/b", "a\\b", "..", ".", "", "a\x00b"])
def test_stock_name_cannot_escape_the_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], stock: str
) -> None:
    module = load_script("analyze-stock", "run_manifest")
    monkeypatch.setenv("INVAGENT_OUTPUT_DIR", str(tmp_path))

    with pytest.raises(SystemExit) as raised:
        module.main([stock, "--run-id", "r1", "--date", "2026-10-10", "--step", "6=ok"])

    assert raised.value.code == 2
    assert "종목명" in capsys.readouterr().err
    assert not any(p.is_file() for p in tmp_path.rglob("*"))
