from pathlib import Path

from invagent.core.archive import archive_root, dated_files, dated_path


def test_dated_path_nests_by_year_and_month() -> None:
    root = Path("/tmp/outputs/portfolio")

    assert dated_path(root, "2026-10-04") == Path("/tmp/outputs/portfolio/2026/10/2026-10-04.md")


def test_dated_files_lists_every_month_in_date_order(tmp_path: Path) -> None:
    for rel in ("2026/09/2026-09-30.md", "2026/10/2026-10-01.md", "2025/12/2025-12-31.md"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x", encoding="utf-8")

    assert [p.stem for p in dated_files(tmp_path)] == ["2025-12-31", "2026-09-30", "2026-10-01"]


def test_dated_files_skips_files_outside_the_dated_layout(tmp_path: Path) -> None:
    month = tmp_path / "2026" / "09"
    month.mkdir(parents=True)
    (month / "2026-09-30.md").write_text("x", encoding="utf-8")
    (month / "backup.md").write_text("x", encoding="utf-8")
    (month / "2026-09-07_drive.csv").write_text("x", encoding="utf-8")
    # 파일 날짜가 폴더와 다르면 잘못 놓인 파일이다
    (month / "2026-10-01.md").write_text("x", encoding="utf-8")
    (tmp_path / "themes").mkdir()
    (tmp_path / "themes" / "2026-09-29.md").write_text("x", encoding="utf-8")
    (tmp_path / "monthly_context.md").write_text("x", encoding="utf-8")

    assert [p.name for p in dated_files(tmp_path)] == ["2026-09-30.md"]


def test_dated_files_on_missing_root_is_empty(tmp_path: Path) -> None:
    assert dated_files(tmp_path / "missing") == []


def test_archive_root_climbs_out_of_the_month_folder() -> None:
    assert archive_root(Path("/o/portfolio/2026/10/2026-10-04.md")) == Path("/o/portfolio")
    # 날짜 폴더 밖의 파일은 그 부모가 곧 루트다
    assert archive_root(Path("/o/portfolio/today.md")) == Path("/o/portfolio")
