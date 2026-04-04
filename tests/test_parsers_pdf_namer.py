"""Tests for the refactored PDFNamer."""

import pytest

from invagent.parsers.pdf_namer import PDFNamer


@pytest.fixture
def namer() -> PDFNamer:
    return PDFNamer()


def test_company_report_from_explicit_prefix(namer: PDFNamer) -> None:
    result = namer.get_filename(
        "기업_NAVER_한차례 낮아진 박스권 흐름 전망_한화투자증권_260403.pdf"
    )
    assert result == "기업_NAVER_한화투자_2026-04-03.pdf"


def test_company_report_from_ticker_suffix_format(namer: PDFNamer) -> None:
    result = namer.get_filename("CJ ENM［035760］_20260206_Samsung_1070683.pdf")
    assert result == "기업_CJ_ENM_삼성_2026-02-06.pdf"


def test_company_report_from_author_pattern(namer: PDFNamer) -> None:
    result = namer.get_filename(
        "최광식_HD현대중공업_수주 모멘텀 재확인_다올투자증권_260403.pdf"
    )
    assert result == "기업_HD현대중공업_다올투자_2026-04-03.pdf"


def test_company_report_without_brokerage_uses_placeholder(namer: PDFNamer) -> None:
    result = namer.get_filename("데브시스터즈_기업설명회IR_개최_20260325.pdf")
    assert result == "기업_데브시스터즈_미상_2026-03-25.pdf"


def test_company_report_without_date_keeps_category(namer: PDFNamer) -> None:
    result = namer.get_filename("[하이브]_BTS_월드투어_추정치_상향.pdf")
    assert result == "기업_하이브_미상_.pdf"


def test_industry_report_from_explicit_prefix(namer: PDFNamer) -> None:
    result = namer.get_filename(
        "산업_자동차_IBK투자증권_IBKS Monthly 자동차2차전지_2026-04-03.pdf"
    )
    assert result == "산업_자동차_IBK투자_IBKS_Monthly_자동차2차전지_2026-04-03.pdf"


def test_industry_report_from_sector_keyword(namer: PDFNamer) -> None:
    result = namer.get_filename(
        "최광식_조선_지금 LNG선을 발주해야 하는 이유_다올투자증권_260403.pdf"
    )
    assert result == "산업_조선_다올투자_지금_LNG선을_발주해야_하는_이유_2026-04-03.pdf"


def test_market_report_keeps_original_stem(namer: PDFNamer) -> None:
    result = namer.get_filename("[QWER] 4월 1주차 - Bull-Bear 베타 + 리비전 전략.pdf")
    assert result == "시황_[QWER]_4월_1주차_-_Bull-Bear_베타_+_리비전_전략.pdf"


def test_market_report_detected_before_other_categories(namer: PDFNamer) -> None:
    result = namer.get_filename(
        "Hana_China_Weekly_신차_효과_및_유가_급등_수혜_가능한_전기차_기업에_주목_하나증권_260403.pdf"
    )
    assert result.startswith("시황_")


def test_other_report_gets_prefixed(namer: PDFNamer) -> None:
    result = namer.get_filename("bbs_1770331977641.pdf")
    assert result == "기타_bbs_1770331977641.pdf"


def test_non_pdf_filename_is_returned_as_is(namer: PDFNamer) -> None:
    assert namer.get_filename("notes.txt") == "notes.txt"
