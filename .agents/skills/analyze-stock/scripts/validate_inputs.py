#!/usr/bin/env python3
"""Validate dated investment-decision inputs carried in JSON.

Each input is an envelope with value, provenance, observation status, and data
semantics.  Numeric zero remains a value; only an absent ``value`` key or an
explicit non-``ok`` status represents missing data.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

STATUSES = {"ok", "missing", "error", "stale", "market_closed"}
KINDS = {"actual", "estimate", "not_applicable"}
BASES = {"consolidated", "separate", "not_applicable"}


class InputValidationError(ValueError):
    """Raised when decision inputs cannot be interpreted safely."""


def _date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise InputValidationError(f"{field} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise InputValidationError(f"{field} must be an ISO date") from exc


def _datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise InputValidationError(f"{field} must be an ISO datetime with timezone")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InputValidationError(f"{field} must be an ISO datetime with timezone") from exc
    if parsed.tzinfo is None:
        raise InputValidationError(f"{field} must include timezone")
    return parsed


def validate_envelope(name: str, item: Any, decision_asof: date) -> tuple[Any, list[str]]:
    """Validate one source envelope and return its value plus warnings."""
    if not isinstance(item, dict):
        raise InputValidationError(f"inputs.{name} must be an object")
    status = item.get("status")
    if status not in STATUSES:
        raise InputValidationError(f"inputs.{name}.status must be one of {sorted(STATUSES)}")

    warnings: list[str] = []
    if status == "ok":
        if "value" not in item:
            raise InputValidationError(f"inputs.{name}.value is required when status is ok")
        if item["value"] is None:
            raise InputValidationError(f"inputs.{name}.value cannot be null when status is ok")
        source = item.get("source")
        if not isinstance(source, str) or not source.strip():
            raise InputValidationError(f"inputs.{name}.source is required when status is ok")
        observed = _date(item.get("asof"), f"inputs.{name}.asof")
        fetched = _datetime(item.get("fetched_at"), f"inputs.{name}.fetched_at")
        if observed > decision_asof:
            raise InputValidationError(f"inputs.{name}.asof cannot be after decision asof")
        if fetched.date() < observed:
            raise InputValidationError(f"inputs.{name}.fetched_at cannot precede its asof date")
    else:
        if "value" in item and item["value"] is not None:
            raise InputValidationError(
                f"inputs.{name}.value must be null or absent when status is {status}"
            )
        observed = _date(item["asof"], f"inputs.{name}.asof") if item.get("asof") else None
        if observed and observed > decision_asof:
            raise InputValidationError(f"inputs.{name}.asof cannot be after decision asof")
        if item.get("fetched_at") is not None:
            _datetime(item["fetched_at"], f"inputs.{name}.fetched_at")

    kind = item.get("kind")
    if kind not in KINDS:
        raise InputValidationError(f"inputs.{name}.kind must distinguish actual/estimate/not_applicable")
    basis = item.get("basis")
    if basis not in BASES:
        raise InputValidationError(
            f"inputs.{name}.basis must distinguish consolidated/separate/not_applicable"
        )
    if kind == "estimate" and status == "ok":
        warnings.append(f"{name}: estimate, not an actual result")
    if basis == "separate" and status == "ok":
        warnings.append(f"{name}: separate-company basis")
    return item.get("value") if status == "ok" else None, warnings


def validate_document(payload: Any) -> dict[str, Any]:
    """Validate a complete input bundle without silently filling missing fields."""
    if not isinstance(payload, dict):
        raise InputValidationError("document must be an object")
    decision_asof = _date(payload.get("asof"), "asof")
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        raise InputValidationError("inputs must be an object")
    required = payload.get("required", list(inputs))
    if not isinstance(required, list) or not all(isinstance(v, str) for v in required):
        raise InputValidationError("required must be a list of input names")

    absent = [name for name in required if name not in inputs]
    errors = [f"inputs.{name} is absent" for name in absent]
    values: dict[str, Any] = {}
    observations: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for name, item in inputs.items():
        try:
            value, item_warnings = validate_envelope(name, item, decision_asof)
        except InputValidationError as exc:
            errors.append(str(exc))
            continue
        values[name] = value
        observations[name] = {
            "status": item["status"],
            "source": item.get("source"),
            "asof": item.get("asof"),
            "fetched_at": item.get("fetched_at"),
            "kind": item["kind"],
            "basis": item["basis"],
        }
        warnings.extend(item_warnings)
        if name in required and item["status"] != "ok":
            errors.append(f"inputs.{name} is required but status is {item['status']}")

    return {
        "valid": not errors,
        "asof": decision_asof.isoformat(),
        "values": values,
        "observations": observations,
        "errors": errors,
        "warnings": warnings,
    }


def _read_json(path: str | None) -> Any:
    text = Path(path).read_text(encoding="utf-8") if path else sys.stdin.read()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputValidationError(f"invalid JSON: {exc.msg}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate sourced decision inputs")
    parser.add_argument("input", nargs="?", help="JSON file; stdin when omitted")
    args = parser.parse_args(argv)
    try:
        result = validate_document(_read_json(args.input))
    except (InputValidationError, OSError) as exc:
        print(json.dumps({"valid": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    sys.exit(main())
