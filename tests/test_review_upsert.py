"""`monthly-investment-review/scripts/review_upsert.py` — 월간 리뷰 블록 교체."""

import json

import pytest

from tests.conftest import load_script

BEGIN = "<!-- invagent-review:2026-03:begin -->"
END = "<!-- invagent-review:2026-03:end -->"


def test_backslashes_in_the_proposed_body_are_kept_verbatim() -> None:
    """본문의 역슬래시(`\\1`, `\\d`, 윈도 경로)는 정규식 치환 템플릿으로 해석되지 않는다."""
    module = load_script("monthly-investment-review", "review_upsert")
    existing = f"머리\n\n{BEGIN}\n옛 리뷰\n{END}\n\n꼬리\n"
    proposed = r"그룹 \1 패턴 \d+ 경로 C:\Users\me\new 줄바꿈 \n"

    out = module.upsert(existing, proposed, "2026-03")

    assert out == f"머리\n\n{BEGIN}\n{proposed}\n{END}\n\n꼬리\n"


def test_a_month_without_a_block_is_appended_after_the_body() -> None:
    """해당 월 블록이 없으면 본문 끝에 빈 줄 하나를 두고 새 블록을 붙인다."""
    module = load_script("monthly-investment-review", "review_upsert")

    out = module.upsert("투자일지 본문\n\n\n", "  새 리뷰  \n", "2026-03")

    assert out == f"투자일지 본문\n\n{BEGIN}\n새 리뷰\n{END}\n"


def test_replacing_a_month_keeps_the_body_and_other_months() -> None:
    """같은 월 블록만 바꾸고 주변 본문과 다른 월 블록은 그대로 둔다."""
    module = load_script("monthly-investment-review", "review_upsert")
    feb = "<!-- invagent-review:2026-02:begin -->\n2월 리뷰\n<!-- invagent-review:2026-02:end -->"
    existing = f"머리\n\n{feb}\n\n중간\n\n{BEGIN}\n옛 3월\n{END}\n\n꼬리\n"

    out = module.upsert(existing, "새 3월", "2026-03")

    assert out == f"머리\n\n{feb}\n\n중간\n\n{BEGIN}\n새 3월\n{END}\n\n꼬리\n"


@pytest.mark.parametrize("month", ["2026-3", "202603", "2026/03", "26-03"])
def test_a_malformed_month_is_rejected(month: str) -> None:
    module = load_script("monthly-investment-review", "review_upsert")

    with pytest.raises(ValueError):
        module.upsert("본문", "리뷰", month)


def _paths(tmp_path, existing: str):
    src = tmp_path / "existing.md"
    src.write_text(existing, encoding="utf-8")
    proposed = tmp_path / "proposed.md"
    proposed.write_text("새 리뷰", encoding="utf-8")
    out = tmp_path / "nested" / "prepared.md"
    return src, proposed, out


def test_main_rejects_a_bad_month_with_exit_2_and_ok_false(tmp_path, capsys) -> None:
    module = load_script("monthly-investment-review", "review_upsert")
    src, proposed, out = _paths(tmp_path, "본문")

    code = module.main(["--existing", str(src), "--proposed", str(proposed),
                        "--month", "2026-3", "--prepared-out", str(out), "--json"])

    assert code == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert not out.exists()


@pytest.mark.parametrize(
    ("existing", "replaced"),
    [("본문", False), (f"본문\n\n{BEGIN}\n옛 리뷰\n{END}\n", True)],
)
def test_main_writes_prepared_out_and_reports_replaced(tmp_path, capsys, existing, replaced) -> None:
    module = load_script("monthly-investment-review", "review_upsert")
    src, proposed, out = _paths(tmp_path, existing)

    code = module.main(["--existing", str(src), "--proposed", str(proposed),
                        "--month", "2026-03", "--prepared-out", str(out), "--json"])

    assert code == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "month": "2026-03", "replaced": replaced}
    assert f"{BEGIN}\n새 리뷰\n{END}" in out.read_text(encoding="utf-8")
    assert src.read_text(encoding="utf-8") == existing


def test_main_without_json_prints_the_prepared_path(tmp_path, capsys) -> None:
    module = load_script("monthly-investment-review", "review_upsert")
    src, proposed, out = _paths(tmp_path, "본문")

    assert module.main(["--existing", str(src), "--proposed", str(proposed),
                        "--month", "2026-03", "--prepared-out", str(out)]) == 0
    assert capsys.readouterr().out.strip() == str(out)
