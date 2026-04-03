"""
PDF 파일명 규칙을 관리하는 PDFNamer 클래스.

이 모듈은 다운로드된 PDF 파일에 일관된 이름을 지정하기 위한
규칙을 정의합니다.
"""

import re


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
