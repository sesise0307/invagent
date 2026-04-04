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
        assert result == "기업_NAVER_한화투자_2026-04-03.pdf"

    def test_stock_with_english_compound_name(self, namer):
        result = namer.get_filename(
            "기업_JYP_Ent_1Q26_프리뷰_견조한_YoY_실적_성장_전망_유안타증권_260403.pdf"
        )
        assert result == "기업_JYP_유안타_2026-04-03.pdf"

    def test_stock_with_korean_name(self, namer):
        result = namer.get_filename(
            "기업_삼성전기_MLCC_임베디드_PCB의_미래_SK증권_260403.pdf"
        )
        assert result == "기업_삼성전기_SK_2026-04-03.pdf"

    def test_stock_with_long_title(self, namer):
        result = namer.get_filename(
            "기업_카카오_대형_파트너십을_통한_서비스_확장_기회_클_한화투자증권_260403.pdf"
        )
        assert result == "기업_카카오_한화투자_2026-04-03.pdf"


class TestPatternB:
    """산업_ 시작 파일명 패턴 테스트."""

    def test_basic_sector_report(self, namer):
        result = namer.get_filename(
            "산업_건설_해외_EPC_기업_실적_및_동향_유진투자증권_260403.pdf"
        )
        assert result == "산업_건설_유진투자_해외_EPC_기업_실적_및_동향_2026-04-03.pdf"

    def test_sector_auto(self, namer):
        result = namer.get_filename(
            "산업_자동차_3월_미국_신차_판매_유진투자증권_260403.pdf"
        )
        assert result == "산업_자동차_유진투자_3월_미국_신차_판매_2026-04-03.pdf"

    def test_sector_semiconductor(self, namer):
        result = namer.get_filename(
            "산업_반도체_클로드_코드_소스_유출에_나타난_시사점_AI_유진투자증권_260403.pdf"
        )
        assert result == "산업_반도체_유진투자_클로드_코드_소스_유출에_나타난_시사점_AI_2026-04-03.pdf"


class TestPatternC:
    """종목코드 대괄호 형식 패턴 테스트."""

    def test_ticker_daishin(self, namer):
        result = namer.get_filename("넥스틴［348210］_20260403_Daishin_1084839.pdf")
        assert result == "기업_넥스틴_대신_2026-04-03.pdf"

    def test_ticker_meritz(self, namer):
        result = namer.get_filename("DL이앤씨［375500］_20260402_MERITZ_1084535.pdf")
        assert result == "기업_DL이앤씨_메리츠_2026-04-02.pdf"

    def test_ticker_ibk(self, namer):
        result = namer.get_filename("한올바이오파마［009420］_20260403_IBK_1084659.pdf")
        assert result == "기업_한올바이오파마_IBK투자_2026-04-03.pdf"

    def test_ticker_hanwha(self, namer):
        result = namer.get_filename("코스맥스［192820］_20260402_Hanwha_1084534.pdf")
        assert result == "기업_코스맥스_한화투자_2026-04-02.pdf"

    def test_ticker_kb(self, namer):
        result = namer.get_filename("HD한국조선해양［009540］_20260402_KB_1084543.pdf")
        assert result == "기업_HD한국조선해양_KB_2026-04-02.pdf"

    def test_ticker_unknown_brokerage(self, namer):
        """매핑에 없는 증권사 코드는 원본 코드 그대로 사용."""
        result = namer.get_filename("테스트종목［123456］_20260403_UNKNOWN_9999999.pdf")
        assert result == "기업_테스트종목_UNKNOWN_2026-04-03.pdf"


class TestPatternE:
    """작성자_기업_리포트제목_증권사_날짜 패턴 테스트."""

    def test_simple_author_with_company(self, namer):
        result = namer.get_filename(
            "김선우_삼성전자_강력한_아웃퍼폼_예상_메리츠증권_260403.pdf"
        )
        assert result == "기업_삼성전자_메리츠_2026-04-03.pdf"

    def test_author_with_korean_company(self, namer):
        result = namer.get_filename(
            "남성현_GS피앤엘_1분기_실적_개선은_추정이_아닌_확정_IBK투자증권_260403.pdf"
        )
        assert result == "기업_GS피앤엘_IBK투자_2026-04-03.pdf"

    def test_author_with_english_report_type(self, namer):
        """2번째 토큰이 영문(Initiation)이면 3번째 토큰을 기업으로 사용."""
        result = namer.get_filename(
            "김진형_Initiation_넥스틴_바닥은_다졌고_위를_바라볼_때_대신증권_260403.pdf"
        )
        assert result == "기업_넥스틴_대신_2026-04-03.pdf"

    def test_non_korean_name_first_token_falls_through(self, namer):
        """첫 토큰이 한국인 이름이 아니면 Pattern E 미적용 → 기타."""
        filename = "SK_증권_박형우_삼성전기_600,000원_MLCC_임베디드_PCB의_미래_SK증권_260403.pdf"
        assert namer.get_filename(filename) == f"기타_{_preprocess(filename)}"

    def test_brokerage_prefix_author_falls_through(self, namer):
        """교보증권_권우정 형태 (첫 토큰이 증권사명) → 기타."""
        filename = "교보증권_권우정_화장품_미국_아마존_빅스프링_세일_약진_및_수출_데이터_호조_교보증권_260403.pdf"
        assert namer.get_filename(filename) == f"기타_{filename}"


class TestPatternF:
    """날짜 선행 형식 (yyyymmdd_종목_증권사) 패턴 테스트."""

    def test_date_leading_stock_short_brokerage(self, namer):
        result = namer.get_filename("20260402_삼성SDI_유안타.pdf")
        assert result == "기업_삼성SDI_유안타_2026-04-02.pdf"

    def test_date_leading_stock_another(self, namer):
        result = namer.get_filename("20260303_올릭스_신한.pdf")
        assert result == "기업_올릭스_신한_2026-03-03.pdf"

    def test_date_leading_with_brokerage_suffix(self, namer):
        """증권사명에 '증권'이 붙어 있으면 제거."""
        result = namer.get_filename("20260303_에이프릴바이오_다올투자증권.pdf")
        assert result == "기업_에이프릴바이오_다올투자_2026-03-03.pdf"

    def test_date_leading_bnk(self, namer):
        result = namer.get_filename("20260227_이수페타시스_BNK.pdf")
        assert result == "기업_이수페타시스_BNK_2026-02-27.pdf"


class TestPatternSiwhang:
    """시황 리포트 패턴 테스트."""

    def test_daily_keyword(self, namer):
        result = namer.get_filename("Daily_Morning_Brief(2026.04.03).pdf")
        assert result == "시황_Daily_Morning_Brief(2026.04.03).pdf"

    def test_weekly_keyword(self, namer):
        result = namer.get_filename(
            "Hana_China_Weekly_신차_효과_및_유가_급등_수혜_가능한_전기차_기업에_주목_하나증권_260403.pdf"
        )
        assert result == "시황_Hana_China_Weekly_신차_효과_및_유가_급등_수혜_가능한_전기차_기업에_주목_하나증권_260403.pdf"

    def test_jugan_keyword(self, namer):
        result = namer.get_filename(
            "4월_2주_캘박_주간_매크로_테마_캘린더_삼성증권_260403.pdf"
        )
        assert result == "시황_4월_2주_캘박_주간_매크로_테마_캘린더_삼성증권_260403.pdf"

    def test_siwhang_keyword(self, namer):
        result = namer.get_filename("국내주식_마감_시황_26_04_02_No_종전_But_석기.pdf")
        assert result == "시황_국내주식_마감_시황_26_04_02_No_종전_But_석기.pdf"

    def test_qwer_keyword(self, namer):
        """공백 전처리 후 시황 감지."""
        result = namer.get_filename("[QWER] 4월 1주차 - Bull-Bear 베타 + 리비전 전략.pdf")
        assert result.startswith("시황_")

    def test_market_keyword(self, namer):
        result = namer.get_filename("Market_Analyst_이재원_국내주식_마감_시황_유안타증권_260403.pdf")
        assert result == "시황_Market_Analyst_이재원_국내주식_마감_시황_유안타증권_260403.pdf"


class TestPatternD:
    """기타 파일 — 기타_ 접두사 추가 테스트."""

    def test_unrecognized_pdf(self, namer):
        filename = "24d8a78a-72a8-40ad-bada-9d5f0c6ae0c9.pdf"
        assert namer.get_filename(filename) == f"기타_{filename}"

    def test_no_pdf_extension(self, namer):
        filename = "some_document"
        assert namer.get_filename(filename) == filename

    def test_bbs_style(self, namer):
        filename = "bbs_1770071909890.pdf"
        assert namer.get_filename(filename) == f"기타_{filename}"


class TestPreprocessing:
    """파일명 전처리 테스트."""

    def test_spaces_converted_for_market_match(self, namer):
        """공백이 언더스코어로 치환된 후 시황 키워드 매칭."""
        result = namer.get_filename("Daily Morning Brief.pdf")
        assert result == "시황_Daily_Morning_Brief.pdf"

    def test_comma_converted(self, namer):
        """콤마가 언더스코어로 치환."""
        filename = "SK_증권_박형우_삼성전기_600,000원_MLCC_임베디드_PCB의_미래_SK증권_260403.pdf"
        result = namer.get_filename(filename)
        assert "기타_" in result
        assert "," not in result


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


def _preprocess(filename: str) -> str:
    """테스트 헬퍼: 전처리된 파일명 반환."""
    if filename.endswith(".pdf"):
        stem = filename[:-4].replace(" ", "_").replace(",", "_")
        return stem + ".pdf"
    return filename
