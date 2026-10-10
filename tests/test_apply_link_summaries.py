"""daily-digest `apply_link_summaries.py` — 링크 요약 마커 치환."""

from __future__ import annotations

from tests.conftest import load_script


def test_apply_link_summaries_replaces_marker_with_sidecar(tmp_path) -> None:
    """요약 대기 마커를 원문 옆 요약 파일 내용으로 바꾸고 파일 줄은 남긴다"""
    module = load_script("daily-digest", "apply_link_summaries")
    body = tmp_path / "example.com_43ecaebf74.md"
    body.write_text("URL: https://example.com/report\n\n원문\n", encoding="utf-8")
    (tmp_path / "example.com_43ecaebf74.summary.md").write_text(
        "요약: 예시 「리포트」\n- 핵심: 매출 +10%\n- 결론: 유지\n", encoding="utf-8"
    )
    raw = tmp_path / "raw.md"
    raw.write_text(
        "링크:\n> URL: https://example.com/report\n"
        f"> 파일: {body}\n> [요약 대기]\n\n다음 메시지\n",
        encoding="utf-8",
    )

    assert module.main([str(raw)]) == 0

    assert raw.read_text(encoding="utf-8") == (
        "링크:\n> URL: https://example.com/report\n"
        f"> 파일: {body}\n"
        "> 요약: 예시 「리포트」\n> - 핵심: 매출 +10%\n> - 결론: 유지\n\n다음 메시지\n"
    )


def test_apply_link_summaries_reads_sidecar_next_to_pdf_body(tmp_path) -> None:
    """PDF로 저장된 링크(`X.pdf`)도 옆의 `X.summary.md`로 마커를 바꾼다"""
    module = load_script("daily-digest", "apply_link_summaries")
    body = tmp_path / "vo.la_167396fe47.pdf"
    body.write_bytes(b"%PDF-1.7\n")
    (tmp_path / "vo.la_167396fe47.summary.md").write_text("요약: 메리츠 「인텍플러스」\n", encoding="utf-8")
    raw = tmp_path / "raw.md"
    raw.write_text(f"> URL: https://vo.la/zapQlKL\n> 파일: {body}\n> [요약 대기]\n", encoding="utf-8")

    assert module.main([str(raw)]) == 0

    assert raw.read_text(encoding="utf-8") == (
        f"> URL: https://vo.la/zapQlKL\n> 파일: {body}\n> 요약: 메리츠 「인텍플러스」\n"
    )


def test_apply_link_summaries_leaves_marker_when_summary_is_missing(tmp_path) -> None:
    """요약 파일이 아직 없으면 마커를 그대로 두어 다시 돌려도 안전하다"""
    module = load_script("daily-digest", "apply_link_summaries")
    body = tmp_path / "news.example_0123456789.md"
    body.write_text("URL: https://news.example/a\n\n원문\n", encoding="utf-8")
    original = f"> 파일: {body}\n> [요약 대기]\n"
    raw = tmp_path / "raw.md"
    raw.write_text(original, encoding="utf-8")

    assert module.main([str(raw)]) == 0

    assert raw.read_text(encoding="utf-8") == original


def test_apply_link_summaries_reports_counts_without_summary_text(tmp_path, capsys) -> None:
    """출력은 반영·대기 건수와 대기 중인 원문 경로뿐이고 요약 내용은 찍지 않는다"""
    module = load_script("daily-digest", "apply_link_summaries")
    done = tmp_path / "done.example_aaaaaaaaaa.md"
    done.write_text("원문", encoding="utf-8")
    (tmp_path / "done.example_aaaaaaaaaa.summary.md").write_text(
        "요약: 비밀 요약 문장\n", encoding="utf-8"
    )
    pending = tmp_path / "wait.example_bbbbbbbbbb.md"
    pending.write_text("원문", encoding="utf-8")
    raw = tmp_path / "raw.md"
    raw.write_text(
        f"> 파일: {done}\n> [요약 대기]\n\n> 파일: {pending}\n> [요약 대기]\n",
        encoding="utf-8",
    )

    assert module.main([str(raw)]) == 0

    assert capsys.readouterr().out == f"반영 1건 · 대기 1건\n대기: {pending}\n"
