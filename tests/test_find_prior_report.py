"""analyze-stock `find_prior_report.py` — 이전 보고서 상속 경로."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import load_script

PRIOR_REPORT_FILES = (
    "종목/ㅇ/율촌화학_2026-08-14.md",
    "종목/ㅇ/율촌화학_2026-07-02.md",
    "종목/ㄱ/기가비스_2026-08-11.md",
    "산업/2026-08-11_기판검사장비_기가비스_인텍플러스_비교.md",
    "산업/2026-08-09_반도체부품주_섹터리포트_분석.md",
    "기타/2026-08_투자전략.md",
)


def _build_reports_dir(root: Path) -> Path:
    reports = root / "reports"
    reports.mkdir(parents=True)
    for name in PRIOR_REPORT_FILES:
        path = reports / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {name}\n", encoding="utf-8")
    # 루트에 날짜 디렉터리로 PDF가 쌓인다. 하위는 스캔 대상이 아니다.
    (reports / "2026-08-14").mkdir()
    (reports / "2026-08-14" / "2026-08-14_율촌화학_함정.md").write_text("x", encoding="utf-8")
    return reports


def test_find_prior_report_inherits_latest_own_report(tmp_path: Path) -> None:
    module = load_script("analyze-stock", "find_prior_report")
    reports = _build_reports_dir(tmp_path)

    result = module.lookup(reports, "율촌화학", "2026-09-01")

    assert result.inherit is not None
    assert result.inherit.path.name == "율촌화학_2026-08-14.md"
    assert [r.path.name for r in result.previous] == ["율촌화학_2026-07-02.md"]
    assert result.since == "2026-08-14"
    assert result.target == reports / "종목" / "ㅇ" / "율촌화학_2026-09-01.md"
    assert result.same_path is False


def test_find_prior_report_treats_comparison_report_as_related(tmp_path: Path) -> None:
    """산업·기타 보고서는 다른 종목 내용도 담겼다 — 참고만 하고 원본을 유지한다."""
    module = load_script("analyze-stock", "find_prior_report")
    reports = _build_reports_dir(tmp_path)

    result = module.lookup(reports, "기가비스", "2026-09-01")

    assert result.inherit is not None
    assert result.inherit.path.name == "기가비스_2026-08-11.md"
    assert [r.path.name for r in result.related] == [
        "2026-08-11_기판검사장비_기가비스_인텍플러스_비교.md"
    ]


@pytest.mark.parametrize(
    ("stock", "expected_dir"),
    [
        ("율촌화학", "ㅇ"),
        ("덕산네오룩스", "ㄷ"),
        ("까뮤이앤씨", "ㄱ"),  # 쌍자음은 평자음 폴더로 접는다
        ("OCI홀딩스", "A-Z"),
    ],
)
def test_find_prior_report_target_routes_by_chosung(
    tmp_path: Path, stock: str, expected_dir: str
) -> None:
    module = load_script("analyze-stock", "find_prior_report")
    reports = _build_reports_dir(tmp_path)

    target = module.lookup(reports, stock, "2026-09-01").target

    assert target == reports / "종목" / expected_dir / f"{stock}_2026-09-01.md"


def test_find_prior_report_reads_new_filename_convention(tmp_path: Path) -> None:
    """`<종목명>_<날짜>.md`로 바뀐 뒤에도 기존 보고서를 승계 대상으로 잡아야 한다."""
    module = load_script("analyze-stock", "find_prior_report")
    reports = _build_reports_dir(tmp_path)
    (reports / "종목" / "ㅇ").mkdir(parents=True, exist_ok=True)
    (reports / "종목" / "ㅇ" / "와이지-원_2026-08-20.md").write_text("# x\n", encoding="utf-8")

    result = module.lookup(reports, "와이지-원", "2026-09-01")

    assert result.inherit is not None
    assert result.inherit.path.name == "와이지-원_2026-08-20.md"
    assert result.inherit.date == "2026-08-20"
    assert result.since == "2026-08-20"


def test_find_prior_report_ignores_undated_and_pdf_date_dirs(tmp_path: Path) -> None:
    module = load_script("analyze-stock", "find_prior_report")
    reports = _build_reports_dir(tmp_path)

    scanned = {report.path.name for report in module.scan(reports)}

    assert "2026-08_투자전략.md" not in scanned  # 일자 없음
    assert "2026-08-14_율촌화학_함정.md" not in scanned  # PDF 날짜 디렉터리


def test_find_prior_report_flags_same_path_rerun(tmp_path: Path) -> None:
    module = load_script("analyze-stock", "find_prior_report")
    reports = _build_reports_dir(tmp_path)
    rerun = reports / "종목" / "ㅇ" / "에이피알_2026-09-01.md"
    rerun.parent.mkdir(parents=True, exist_ok=True)
    rerun.write_text("# x\n", encoding="utf-8")

    result = module.lookup(reports, "에이피알", "2026-09-01")

    assert result.same_path is True


def test_find_prior_report_main_reports_new_and_inherited(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_script("analyze-stock", "find_prior_report")
    _build_reports_dir(tmp_path)
    monkeypatch.setenv("INVAGENT_OUTPUT_DIR", str(tmp_path))

    assert module.main(["율촌화학", "--today", "2026-09-01"]) == 0
    out = capsys.readouterr().out
    assert "종목/ㅇ/율촌화학_2026-08-14.md" in out
    assert "reports/종목/ㅇ/율촌화학_2026-09-01.md" in out
    assert "증분 기준일(`--since`): 2026-08-14" in out
    assert "과거 중복 후보 1건" in out
    assert "--inherit --expected-sha256" in out
    assert "복사본을 만들지 마라" in out
    # 옛 보고서는 `## 12. 개정 이력`, 현 템플릿은 `## 11. 개정 이력` — 번호가 아니라 절 이름으로 찾는다.
    assert "승계본의 `개정 이력` 절을 반드시 읽어라" in out
    assert "템플릿 섹션 순서로 재배치" in out
    assert "§11에 한 줄로 옮긴 뒤 본문에서 뺀다" in out
    assert "`## 11. 개정 이력`에 `- YYYY-MM-DD: 구분 — 요약` 한 줄" in out
    assert "## 12." not in out.replace("옛 배치는 `## 12.`", "")

    assert module.main(["카카오", "--today", "2026-09-01"]) == 0
    new_out = capsys.readouterr().out
    assert "승계 대상: 없음 (신규 작성)" in new_out
    assert "reports/종목/ㅋ/카카오_2026-09-01.md" in new_out


def _sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_find_prior_report_output_exposes_source_sha256(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_script("analyze-stock", "find_prior_report")
    reports = _build_reports_dir(tmp_path)
    monkeypatch.setenv("INVAGENT_OUTPUT_DIR", str(tmp_path))
    source = reports / "종목" / "ㅇ" / "율촌화학_2026-08-14.md"

    assert module.main(["율촌화학", "--today", "2026-09-01"]) == 0

    assert f"승계본 sha256(`--expected-sha256`): {_sha(source)}" in capsys.readouterr().out


def test_find_prior_report_inherit_moves_prior_report_to_target(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_script("analyze-stock", "find_prior_report")
    reports = _build_reports_dir(tmp_path)
    monkeypatch.setenv("INVAGENT_OUTPUT_DIR", str(tmp_path))
    source = reports / "종목" / "ㅇ" / "율촌화학_2026-08-14.md"
    source.write_text("# 율촌화학\n\n본문 그대로\n", encoding="utf-8")
    digest = _sha(source)

    code = module.main(
        ["율촌화학", "--today", "2026-09-01", "--inherit", "--expected-sha256", digest]
    )

    target = reports / "종목" / "ㅇ" / "율촌화학_2026-09-01.md"
    assert code == 0
    assert not source.exists()
    assert target.read_text(encoding="utf-8") == "# 율촌화학\n\n본문 그대로\n"
    assert sorted(p.name for p in target.parent.glob("율촌화학_*.md")) == [
        "율촌화학_2026-07-02.md",  # 과거 중복 후보는 손대지 않는다
        "율촌화학_2026-09-01.md",
    ]
    assert "reports/종목/ㅇ/율촌화학_2026-09-01.md" in capsys.readouterr().out


def test_find_prior_report_inherit_refuses_stale_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_script("analyze-stock", "find_prior_report")
    reports = _build_reports_dir(tmp_path)
    monkeypatch.setenv("INVAGENT_OUTPUT_DIR", str(tmp_path))
    source = reports / "종목" / "ㅇ" / "율촌화학_2026-08-14.md"
    stale = _sha(source)
    source.write_text("# 읽은 뒤 바뀐 본문\n", encoding="utf-8")

    code = module.main(
        ["율촌화학", "--today", "2026-09-01", "--inherit", "--expected-sha256", stale]
    )

    assert code == 2
    assert source.read_text(encoding="utf-8") == "# 읽은 뒤 바뀐 본문\n"
    assert not (reports / "종목" / "ㅇ" / "율촌화학_2026-09-01.md").exists()
    assert "읽은 뒤 승계본이 바뀌었다" in capsys.readouterr().err


def test_find_prior_report_inherit_refuses_existing_different_target(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """목표 경로에 다른 파일이 이미 있으면 덮지 않는다 — 두 파일 모두 그대로 남는다."""
    module = load_script("analyze-stock", "find_prior_report")
    reports = _build_reports_dir(tmp_path)
    monkeypatch.setenv("INVAGENT_OUTPUT_DIR", str(tmp_path))
    folder = reports / "종목" / "ㅇ"
    source = folder / "율촌화학_2026-09-05.md"  # 목표일보다 늦은 날짜가 승계 대상이 된다
    source.write_text("# 승계본\n", encoding="utf-8")
    target = folder / "율촌화학_2026-09-01.md"
    target.write_text("# 이미 있는 목표\n", encoding="utf-8")

    code = module.main(
        ["율촌화학", "--today", "2026-09-01", "--inherit", "--expected-sha256", _sha(source)]
    )

    assert code == 2
    assert source.read_text(encoding="utf-8") == "# 승계본\n"
    assert target.read_text(encoding="utf-8") == "# 이미 있는 목표\n"
    assert "목표 경로에 다른 파일이 이미 있다" in capsys.readouterr().err


def test_find_prior_report_inherit_same_path_is_noop(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_script("analyze-stock", "find_prior_report")
    reports = _build_reports_dir(tmp_path)
    monkeypatch.setenv("INVAGENT_OUTPUT_DIR", str(tmp_path))
    source = reports / "종목" / "ㅇ" / "율촌화학_2026-08-14.md"
    before = source.read_text(encoding="utf-8")

    code = module.main(
        ["율촌화학", "--today", "2026-08-14", "--inherit", "--expected-sha256", _sha(source)]
    )

    assert code == 0
    assert source.read_text(encoding="utf-8") == before
    assert "이미 목표 경로" in capsys.readouterr().out


def test_find_prior_report_inherit_reports_unremovable_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """목표를 쓴 뒤 원본 삭제가 실패하면 성공으로 말하지 않고 남은 두 파일을 짚는다."""
    module = load_script("analyze-stock", "find_prior_report")
    reports = _build_reports_dir(tmp_path)
    monkeypatch.setenv("INVAGENT_OUTPUT_DIR", str(tmp_path))
    source = reports / "종목" / "ㅇ" / "율촌화학_2026-08-14.md"
    digest = _sha(source)

    def refuse(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "unlink", refuse)
    code = module.main(
        ["율촌화학", "--today", "2026-09-01", "--inherit", "--expected-sha256", digest]
    )

    assert code == 2
    assert source.exists()
    assert (reports / "종목" / "ㅇ" / "율촌화학_2026-09-01.md").read_bytes() == source.read_bytes()
    assert "원본" in capsys.readouterr().err
