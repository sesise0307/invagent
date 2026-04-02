"""
PDF 파일명 규칙을 관리하는 PDFNamer 클래스.

이 모듈은 다운로드된 PDF 파일에 일관된 이름을 지정하기 위한
규칙을 정의합니다.
"""


class PDFNamer:
    """PDF 파일명 규칙을 관리하는 클래스."""

    def __init__(self) -> None:
        """PDFNamer를 초기화합니다."""
        pass

    def get_filename(self, original_filename: str) -> str:
        """
        원본 파일명을 기반으로 통일된 파일명을 생성합니다.

        현재는 원본 파일명을 그대로 반환합니다.
        향후 파일명 정규화 규칙을 추가할 수 있습니다.

        Args:
            original_filename: 원본 파일명

        Returns:
            통일된 파일명
        """
        # 향후 파일명 규칙 적용 예정
        return original_filename
