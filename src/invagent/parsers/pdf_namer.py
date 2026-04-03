"""
PDF 파일명 규칙을 관리하는 PDFNamer 클래스.

이 모듈은 다운로드된 PDF 파일에 일관된 이름을 지정하기 위한
규칙을 정의합니다.
"""

import re


# 한국인 성씨 목록 — Pattern E 작성자 탐지용
_KOREAN_SURNAMES: frozenset[str] = frozenset(
    "김이박최정강조윤장임한오서신권황안송류홍전고문손양배백허유남"
    "심노하곽성차주우구민나지엄원천방공현함변염여추도소석선설마"
    "길연위표명반왕옥육인맹제모사탁봉"
)

# 기업명 자리에 올 수 없는 비기업 토큰 — Pattern E 오분류 방지
_NON_COMPANY_TOKENS: frozenset[str] = frozenset({
    # 섹터
    "음식료", "조선", "인터넷", "반도체", "화장품", "건설", "자동차",
    "바이오", "금융", "통신", "에너지", "방산", "철강", "화학", "게임",
    "부동산", "유틸리티", "미디어", "보험", "은행", "증권", "의류", "유통",
    # 국가/지역
    "미국", "중국", "일본", "유럽", "글로벌", "아시아", "한국",
    # 영문 리포트 시리즈 (2nd 토큰이 영문일 때 3rd 토큰에도 적용)
    "Weekly", "Monthly", "Daily", "Talk", "Check", "View", "Update",
    # 시간/분기
    "1분기", "2분기", "3분기", "4분기",
    # 기타 비기업
    "호르무즈", "이란", "트럼프",
    # 정신승리도, Quant의 등 전략/철학 제목 토큰
    "정신승리도",
})

# 텔레그램 채널별 증권사 코드 → 한국어 이름 매핑 (Pattern C용)
_BROKERAGE_MAP: dict[str, str] = {
    "KB": "KB증권",
    "Samsung": "삼성증권",
    "MERITZ": "메리츠증권",
    "Daishin": "대신증권",
    "Kiwoom": "키움증권",
    "IBK": "IBK투자증권",
    "NH": "NH투자증권",
    "BNK": "BNK투자증권",
    "Hanwha": "한화투자증권",
    "DAOL": "다올투자증권",
    "DS": "DS투자증권",
    "Yuanta": "유안타증권",
    "Hana": "하나증권",
    "LS": "LS증권",
}

# Pattern C: <종목명>［6자리종목코드］_<yyyymmdd>_<증권사코드>_<id>.pdf
_TICKER_PATTERN = re.compile(
    r"^(.+)［\d{6}］_(\d{8})_([^_]+)_\d+\.pdf$"
)


def _normalize_date(date_str: str) -> str:
    """날짜 문자열을 yyyy-mm-dd 형식으로 정규화합니다.

    Args:
        date_str: 날짜 문자열 (yymmdd 또는 yyyymmdd)

    Returns:
        yyyy-mm-dd 형식의 날짜 문자열. 파싱 실패 시 빈 문자열 반환.
    """
    if len(date_str) == 6 and date_str.isdigit():
        # yymmdd → 20yy-mm-dd
        return f"20{date_str[:2]}-{date_str[2:4]}-{date_str[4:6]}"
    if len(date_str) == 8 and date_str.isdigit():
        # yyyymmdd → yyyy-mm-dd
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    return ""


class PDFNamer:
    """PDF 파일명 규칙을 관리하는 클래스."""

    def __init__(self) -> None:
        """PDFNamer를 초기화합니다."""
        pass

    def get_filename(self, original_filename: str) -> str:
        """
        원본 파일명을 기반으로 통일된 파일명을 생성합니다.

        카테고리별 변환 규칙:
        - 종목리포트: 기업_<종목명>_<증권사>_<yyyy-mm-dd>.pdf
        - 산업리포트: 산업_<섹터>_<증권사>_<제목>_<yyyy-mm-dd>.pdf
        - 기타: 원래 파일명 유지

        Args:
            original_filename: 원본 파일명 (.pdf 포함 또는 미포함)

        Returns:
            통일된 파일명
        """
        if not original_filename.endswith(".pdf"):
            return original_filename

        stem = original_filename[:-4]  # .pdf 제거

        # Pattern A: 기업_ 시작
        if stem.startswith("기업_"):
            return self._rename_stock_report(stem)

        # Pattern B: 산업_ 시작
        if stem.startswith("산업_"):
            return self._rename_sector_report(stem)

        # Pattern C: 종목코드 대괄호 형식
        match = _TICKER_PATTERN.match(original_filename)
        if match:
            return self._rename_ticker_format(match)

        # Pattern E: <작성자>_<기업>_<리포트제목>_<증권사>_<날짜> 형식
        result = self._try_rename_author_format(stem)
        if result:
            return result

        # Pattern D: 기타 — 원래 파일명 유지
        return original_filename

    def _rename_stock_report(self, stem: str) -> str:
        """Pattern A: `기업_<종목명>_<제목>_<증권사>_<yymmdd>` → `기업_<종목명>_<증권사>_<yyyy-mm-dd>.pdf`"""
        parts = stem.split("_")
        # 최소 4개 토큰 필요: 기업, 종목명, 증권사, 날짜
        if len(parts) < 4:
            return stem + ".pdf"

        date = _normalize_date(parts[-1])
        if not date:
            return stem + ".pdf"

        brokerage = parts[-2]
        stock_name = parts[1]  # 첫 번째 토큰만 종목명으로 사용
        return f"기업_{stock_name}_{brokerage}_{date}.pdf"

    def _rename_sector_report(self, stem: str) -> str:
        """Pattern B: `산업_<섹터>_<제목>_<증권사>_<yymmdd>` → `산업_<섹터>_<증권사>_<제목>_<yyyy-mm-dd>.pdf`"""
        parts = stem.split("_")
        # 최소 4개 토큰 필요: 산업, 섹터, 증권사, 날짜
        if len(parts) < 4:
            return stem + ".pdf"

        date = _normalize_date(parts[-1])
        if not date:
            return stem + ".pdf"

        brokerage = parts[-2]
        sector = parts[1]
        title_parts = parts[2:-2]  # 섹터와 증권사_날짜 사이
        title = "_".join(title_parts) if title_parts else ""

        if title:
            return f"산업_{sector}_{brokerage}_{title}_{date}.pdf"
        return f"산업_{sector}_{brokerage}_{date}.pdf"

    def _rename_ticker_format(self, match: re.Match) -> str:
        """Pattern C: `<종목명>［ticker］_<yyyymmdd>_<증권사코드>_<id>.pdf` → `기업_<종목명>_<증권사>_<yyyy-mm-dd>.pdf`"""
        stock_name = match.group(1)
        date = _normalize_date(match.group(2))
        brokerage_code = match.group(3)
        brokerage = _BROKERAGE_MAP.get(brokerage_code, brokerage_code)
        return f"기업_{stock_name}_{brokerage}_{date}.pdf"

    def _try_rename_author_format(self, stem: str) -> str | None:
        """Pattern E: `<작성자>_<기업>_<리포트제목>_<증권사>_<yymmdd>` → `기업_<기업>_<증권사>_<yyyy-mm-dd>.pdf`

        작성자는 항상 한국인 이름 1토큰 (성씨로 시작하는 3자 한글).
        2번째 토큰이 영문이면 리포트 타입(Initiation 등)으로 보고 3번째 토큰을 기업으로 사용.
        """
        parts = stem.split("_")
        # 최소 5개 토큰 필요: 작성자, 기업, 제목, 증권사, 날짜
        if len(parts) < 5:
            return None

        # 첫 토큰이 한국인 이름인지 확인: 정확히 3자 한글 + 첫 글자가 성씨
        author = parts[0]
        if not (len(author) == 3 and author.isalpha() and all('\uAC00' <= c <= '\uD7A3' for c in author)):
            return None
        if author[0] not in _KOREAN_SURNAMES:
            return None

        # 날짜 확인
        date = _normalize_date(parts[-1])
        if not date:
            return None

        brokerage = parts[-2]
        second_token = parts[1]

        # 2번째 토큰이 영문(리포트 타입)이면 3번째 토큰을 기업으로 사용
        if second_token.isascii() and second_token.isalpha():
            if len(parts) < 6:
                return None
            stock_name = parts[2]
        else:
            stock_name = second_token

        # 비기업 토큰이 기업 자리에 오면 기타 처리
        if stock_name in _NON_COMPANY_TOKENS:
            return None

        # 영문+한글조사 형태(예: Quant의, Market의)는 기업명 아님
        if len(stock_name) > 1 and stock_name[-1] == '의' and stock_name[:-1].isascii():
            return None

        return f"기업_{stock_name}_{brokerage}_{date}.pdf"
