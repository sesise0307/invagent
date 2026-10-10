"""analyze-stock `validate_inputs.py` — 밸류에이션 입력 봉투 검증."""

from __future__ import annotations

import json

from tests.conftest import load_script


def test_validate_inputs_rejects_valuation_shaped_input_naming_the_envelope(capsys, tmp_path):
    module = load_script("analyze-stock", "validate_inputs")
    valuation_shaped = {
        "asof": "2026-10-07",
        "current_price": 1217000,
        "consensus": {"low": 1750000, "average": 1865833, "high": 2000000},
    }
    path = tmp_path / "valuation-input.json"
    path.write_text(json.dumps(valuation_shaped), encoding="utf-8")

    exit_code = module.main([str(path)])

    result = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert result["valid"] is False
    message = result["errors"][0]
    for field in ("inputs", "value", "status", "source", "fetched_at", "kind", "basis"):
        assert field in message, f"오류 메시지에 기대 형식의 {field}가 없다: {message}"
