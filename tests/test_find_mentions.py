"""analyze-stock `find_mentions.py` — 브리핑 아카이브 언급 위치 검색."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import load_script

INDEX_MD = """# 월간 누적 컨텍스트 (인덱스)

## 반복 등장 신호

- **⭐ 알파전자 = 증설 사이클 진입 (8/9)** → [[themes/알파전자-증설]] · 캐파 2배 · 최근(2026-08-13): 가동률 92%
- **⛔ 베타파마 = 임상 실패, 회피** · 후속 신호 없음
- **감마엔터** · 신작 대기 · 최근(08-01): 예약 판매 호조
"""

THEME_MD = """# 알파전자 증설

- **알파전자 증설** (2026-06-01~): 캐파 2배 증설
  - 2026-06-20: 착공. 델타중공업이 EPC 수주.
  - 2026-08-13: **⭐ 가동률 92% 확인** — 알파전자 증설분 조기 램프업.
  - 2026-07-05: 장비 발주 완료. 알파전자 공시.
"""

DAILY_MD = """# 텔레그램 데일리 브리핑 — 2026-08-13

## 📂 섹터별 / 종목별 정리

### 반도체

- **알파전자 (123456), 2Q OP +48% 서프라이즈**
  - 매출 1.2조, OPM 18.4%
  - 투자 시사점: 증설 효과가 실적으로 확인됨
- **엡실론소재**, 원재료 단가 하락 수혜
  - 알파전자 납품 비중 40%
  - 투자 시사점: 전방 확인 필요

## 💡 오늘의 투자 조언

- 알전은 베이스 돌파 대기.
"""

BROKEN_MD = """알파전자 관련 메모지만 불릿 구조가 없다.
두 번째 줄.
"""


def _build_telegram_archive(root: Path) -> Path:
    archive = root / "daily-digest"
    (archive / "themes" / "archive").mkdir(parents=True)
    (archive / "2026" / "08").mkdir(parents=True)

    (archive / "monthly_context.md").write_text(INDEX_MD, encoding="utf-8")
    (archive / "monthly_context.md.bak").write_text(INDEX_MD, encoding="utf-8")
    (archive / "themes" / "알파전자-증설.md").write_text(THEME_MD, encoding="utf-8")
    (archive / "themes" / "archive" / "pruned-2026-08-09-정리전-인덱스-전체.md").write_text(
        THEME_MD, encoding="utf-8"
    )
    (archive / "2026" / "08" / "2026-08-13.md").write_text(DAILY_MD, encoding="utf-8")
    (archive / "2026" / "08" / "backup.md").write_text(DAILY_MD, encoding="utf-8")
    return archive


def test_find_mentions_parses_records_per_source(tmp_path: Path) -> None:
    module = load_script("analyze-stock", "find_mentions")
    archive = _build_telegram_archive(tmp_path)

    result = module.collect(archive, ["알파전자"])

    # 인덱스 항목은 현재 유효 판정 — 마커와 테마 포인터를 열로 보존한다
    assert len(result.index_hits) == 1
    assert result.index_hits[0].marker == "⭐"
    assert result.index_hits[0].pointer == "알파전자-증설"

    # 테마 서브불릿은 파일 내 순서가 어긋나도 최신순으로 정렬된다
    assert [r.date for r in result.theme_hits] == ["2026-08-13", "2026-07-05", "2026-06-01"]

    # 일일 브리핑: 헤드 매치가 본문 매치보다 먼저 온다
    daily = result.daily_hits
    assert daily[0].match == "head"
    assert "알파전자" in daily[0].head
    assert daily[0].label == "반도체"
    assert [r.match for r in daily] == ["head", "body"]


def test_find_mentions_groups_indented_block_into_head_record(tmp_path: Path) -> None:
    """`- **종목**` 헤드에 딸린 들여쓴 하위 불릿은 같은 레코드의 본문이다."""
    module = load_script("analyze-stock", "find_mentions")
    archive = _build_telegram_archive(tmp_path)

    result = module.collect(archive, ["엡실론소재"])
    record = result.daily_hits[0]

    assert record.match == "head"
    assert "알파전자 납품 비중 40%" in record.body
    assert "투자 시사점" in record.body


def test_find_mentions_excludes_duplicate_sources(tmp_path: Path) -> None:
    module = load_script("analyze-stock", "find_mentions")
    archive = _build_telegram_archive(tmp_path)

    paths = {hit.path.name for hit in module.collect(archive, ["알파전자"]).all_hits}

    assert "monthly_context.md.bak" not in paths
    assert "backup.md" not in paths
    assert "pruned-2026-08-09-정리전-인덱스-전체.md" not in paths


def test_find_mentions_alias_expands_search(tmp_path: Path) -> None:
    module = load_script("analyze-stock", "find_mentions")
    archive = _build_telegram_archive(tmp_path)

    plain = module.collect(archive, ["알파전자"]).daily_hits
    expanded = module.collect(archive, ["알파전자", "알전"]).daily_hits

    assert len(expanded) == len(plain) + 1
    assert any("알전은 베이스 돌파" in hit.head for hit in expanded)


def test_find_mentions_falls_back_to_line_scan(tmp_path: Path) -> None:
    """레코드 구조가 없는 파일도 놓치지 않는다."""
    module = load_script("analyze-stock", "find_mentions")
    archive = _build_telegram_archive(tmp_path)
    (archive / "themes" / "깨진테마.md").write_text(BROKEN_MD, encoding="utf-8")

    hits = [h for h in module.collect(archive, ["알파전자"]).theme_hits if h.path.name == "깨진테마.md"]

    assert len(hits) == 1
    assert hits[0].line == 1


def test_find_mentions_output_is_locator_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_script("analyze-stock", "find_mentions")
    _build_telegram_archive(tmp_path)
    monkeypatch.setenv("INVAGENT_OUTPUT_DIR", str(tmp_path))

    assert module.main(["알파전자", "--top", "1"]) == 0
    out = capsys.readouterr().out

    assert "## 인덱스 현재 판정" in out
    assert "themes/알파전자-증설.md:5" in out  # 경로:줄번호 로케이터
    assert "미표시 2건" in out  # 테마 3건 중 1건만 표시 → 나머지는 월별 집계로
    assert "2026-07 1건" in out and "2026-06 1건" in out
    # 본문(들여쓴 하위 불릿)은 출력하지 않는다
    assert "OPM 18.4%" not in out

    assert module.main(["없는종목명", "--count-only"]) == 0
    assert "아카이브 언급 없음" in capsys.readouterr().out


def test_find_mentions_since_filter(tmp_path: Path) -> None:
    module = load_script("analyze-stock", "find_mentions")
    archive = _build_telegram_archive(tmp_path)

    result = module.collect(archive, ["알파전자"], since="2026-08-01")

    assert all(hit.date >= "2026-08-01" for hit in result.all_hits)
    assert result.theme_hits and len(result.theme_hits) == 1
