"""analyze-stock `find_reports.py` — 증권사 리포트 PDF 아카이브 검색."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import load_script


@pytest.mark.parametrize(
    ("stock", "expected"),
    [
        ("율촌화학", "ㅇ"),
        ("삼성전자", "ㅅ"),
        ("까뮤이앤씨", "ㄱ"),  # 쌍자음은 평자음 폴더로 접는다
        ("쌍용C&E", "ㅅ"),
        ("SK하이닉스", "A-Z"),
        ("3S", "A-Z"),
    ],
)
def test_find_reports_routes_by_chosung(stock: str, expected: str) -> None:
    module = load_script("analyze-stock", "find_reports")

    assert module.chosung_dir(stock) == expected


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("20260327_[율촌화학] 배터리 포장.pdf", "2026-03-27"),
        ("202602_이수페타시스 26년 경영계획.pdf", "2026-02"),
        ("2020_반도체_후공정패키징_.pdf", "2020"),
        ("20261332_잘못된_월.pdf", None),
        ("24.9.12 iM증권 제약 CDMO.pdf", None),
    ],
)
def test_find_reports_parses_leading_date(filename: str, expected: str | None) -> None:
    module = load_script("analyze-stock", "find_reports")

    assert module.parse_date(filename) == expected


def _build_archive(root: Path) -> None:
    stock = root / "ㅇ" / "율촌화학"
    (stock / "IR자료").mkdir(parents=True)
    for name in (
        "20260327_[율촌화학] 최신.pdf",
        "20240502_율촌화학_구형.pdf",
        "율촌화학_날짜없음.pdf",
    ):
        (stock / name).touch()
    (stock / "IR자료" / "202505_율촌화학 IR.pdf").touch()
    (root / "ㅅ" / "삼성전자").mkdir(parents=True)
    sector = root / "_산업분석" / "화학"
    sector.mkdir(parents=True)
    (sector / "20260101_포장재_율촌화학_비교.pdf").touch()


def test_find_reports_collects_sorted_with_aux_dirs(tmp_path: Path) -> None:
    _build_archive(tmp_path)
    module = load_script("analyze-stock", "find_reports")

    items, dirs, kind = module.collect(tmp_path, "율촌화학")

    assert kind == "exact"
    assert [d.name for d in dirs] == ["율촌화학"]
    # IR자료 하위 + _산업분석 파일명 매칭까지 포함해 최신순
    assert [parsed for parsed, _ in items] == [
        "2026-03-27",
        "2026-01-01",
        "2025-05",
        "2024-05-02",
        None,
    ]
    assert items[2][1].parent.name == "IR자료"
    assert items[1][1].parent.parent.name == "_산업분석"


def test_find_reports_limit_and_missing_stock(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_archive(tmp_path)
    module = load_script("analyze-stock", "find_reports")
    monkeypatch.setenv("INVAGENT_REPORT_ARCHIVE", str(tmp_path))

    assert module.main(["율촌화학", "--limit", "2"]) == 0
    out = capsys.readouterr().out
    assert "선정 2건" in out
    assert "20260327_[율촌화학] 최신.pdf" in out
    assert "20240502_율촌화학_구형.pdf" not in out
    assert "미선정 3건" in out

    # 폴더가 없어도 블로킹하지 않는다
    assert module.main(["없는종목명"]) == 0
    assert "검색 결과 없음" in capsys.readouterr().out


def test_find_reports_archive_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """저장소·스킬 파일에 절대경로를 박지 않기 위한 환경변수 오버라이드."""
    module = load_script("analyze-stock", "find_reports")
    monkeypatch.setenv("INVAGENT_REPORT_ARCHIVE", str(tmp_path))

    assert module.archive_root() == tmp_path
