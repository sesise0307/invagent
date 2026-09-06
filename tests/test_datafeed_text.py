"""`invagent.datafeed.text` — 종목명 비교를 위한 문자열 정규화."""

import unicodedata

from invagent.datafeed import text


def test_nfc_folds_the_decomposed_macos_form() -> None:
    decomposed = unicodedata.normalize("NFD", "삼성전자")

    assert decomposed != "삼성전자"
    assert text.nfc(decomposed) == "삼성전자"


def test_norm_drops_case_whitespace_and_punctuation() -> None:
    assert text.norm(" SK 하이닉스 ") == "sk하이닉스"
    assert text.norm("LG(전자)") == text.norm("LG 전자")
    assert text.norm("에스-원") == text.norm("에스원")
