"""PDFNamer 클래스 테스트."""

import pytest
from invagent.parsers.pdf_namer import PDFNamer


@pytest.fixture
def namer():
    return PDFNamer()


class TestPatternA:
    """기업_ 시작 파일명 패턴 테스트."""

    def test_basic_stock_report(self, namer):
        result = namer.get_filename(
            "기업_NAVER_한차례_낮아진_박스권_흐름_전망_한화투자증권_260403.pdf"
        )
        assert result == "기업_NAVER_한화투자증권_2026-04-03.pdf"

    def test_stock_with_english_compound_name(self, namer):
        result = namer.get_filename(
            "기업_JYP_Ent_1Q26_프리뷰_견조한_YoY_실적_성장_전망_유안타증권_260403.pdf"
        )
        assert result == "기업_JYP_유안타증권_2026-04-03.pdf"

    def test_stock_with_korean_name(self, namer):
        result = namer.get_filename(
            "기업_삼성전기_MLCC_임베디드_PCB의_미래_SK증권_260403.pdf"
        )
        assert result == "기업_삼성전기_SK증권_2026-04-03.pdf"

    def test_stock_with_long_title(self, namer):
        result = namer.get_filename(
            "기업_카카오_대형_파트너십을_통한_서비스_확장_기회_클_한화투자증권_260403.pdf"
        )
        assert result == "기업_카카오_한화투자증권_2026-04-03.pdf"


class TestPatternB:
    """산업_ 시작 파일명 패턴 테스트."""

    def test_basic_sector_report(self, namer):
        result = namer.get_filename(
            "산업_건설_해외_EPC_기업_실적_및_동향_유진투자증권_260403.pdf"
        )
        assert result == "산업_건설_유진투자증권_해외_EPC_기업_실적_및_동향_2026-04-03.pdf"

    def test_sector_auto(self, namer):
        result = namer.get_filename(
            "산업_자동차_3월_미국_신차_판매_유진투자증권_260403.pdf"
        )
        assert result == "산업_자동차_유진투자증권_3월_미국_신차_판매_2026-04-03.pdf"

    def test_sector_semiconductor(self, namer):
        result = namer.get_filename(
            "산업_반도체_클로드_코드_소스_유출에_나타난_시사점_AI_유진투자증권_260403.pdf"
        )
        assert result == "산업_반도체_유진투자증권_클로드_코드_소스_유출에_나타난_시사점_AI_2026-04-03.pdf"


class TestPatternC:
    """종목코드 대괄호 형식 패턴 테스트."""

    def test_ticker_daishin(self, namer):
        result = namer.get_filename("넥스틴［348210］_20260403_Daishin_1084839.pdf")
        assert result == "기업_넥스틴_대신증권_2026-04-03.pdf"

    def test_ticker_meritz(self, namer):
        result = namer.get_filename("DL이앤씨［375500］_20260402_MERITZ_1084535.pdf")
        assert result == "기업_DL이앤씨_메리츠증권_2026-04-02.pdf"

    def test_ticker_ibk(self, namer):
        result = namer.get_filename("한올바이오파마［009420］_20260403_IBK_1084659.pdf")
        assert result == "기업_한올바이오파마_IBK투자증권_2026-04-03.pdf"

    def test_ticker_hanwha(self, namer):
        result = namer.get_filename("코스맥스［192820］_20260402_Hanwha_1084534.pdf")
        assert result == "기업_코스맥스_한화투자증권_2026-04-02.pdf"

    def test_ticker_kb(self, namer):
        result = namer.get_filename("HD한국조선해양［009540］_20260402_KB_1084543.pdf")
        assert result == "기업_HD한국조선해양_KB증권_2026-04-02.pdf"

    def test_ticker_unknown_brokerage(self, namer):
        """매핑에 없는 증권사 코드는 원본 코드 그대로 사용."""
        result = namer.get_filename("테스트종목［123456］_20260403_UNKNOWN_9999999.pdf")
        assert result == "기업_테스트종목_UNKNOWN_2026-04-03.pdf"


class TestPatternD:
    """기타 파일 — 원래 파일명 유지 테스트."""

    def test_daily_brief(self, namer):
        filename = "Daily Morning Brief(2026.04.03).pdf"
        assert namer.get_filename(filename) == filename

    def test_macro_calendar(self, namer):
        filename = "4월_2주_캘박_주간_매크로_테마_캘린더_삼성증권_260403.pdf"
        assert namer.get_filename(filename) == filename

    def test_hana_china_weekly(self, namer):
        filename = "Hana_China_Weekly_신차_효과_및_유가_급등_수혜_가능한_전기차_기업에_주목_하나증권_260403.pdf"
        assert namer.get_filename(filename) == filename

    def test_no_pdf_extension(self, namer):
        filename = "some_document"
        assert namer.get_filename(filename) == filename


class TestDateNormalization:
    """날짜 정규화 테스트."""

    def test_yymmdd_format(self, namer):
        """6자리 날짜 (yymmdd) → yyyy-mm-dd."""
        result = namer.get_filename("기업_NAVER_제목_한화투자증권_260403.pdf")
        assert "2026-04-03" in result

    def test_yyyymmdd_format(self, namer):
        """8자리 날짜 (yyyymmdd) → yyyy-mm-dd."""
        result = namer.get_filename("넥스틴［348210］_20260403_Daishin_1084839.pdf")
        assert "2026-04-03" in result
