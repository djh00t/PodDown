"""Execution-evidence vocabulary and validation for production claims."""

import math
from collections.abc import Mapping
from enum import StrEnum
from typing import cast


class ExecutionMode(StrEnum):
    """Where a render and transcript record was executed."""

    DETERMINISTIC_LOCAL = "deterministic-local"
    HOST_LOCAL = "host-local"
    LIVE_PROVIDER = "live-provider"


class EvidenceKind(StrEnum):
    """Origin of provider evidence retained with an execution record."""

    SYNTHETIC = "synthetic"
    HOST_LOCAL = "host-local"
    PROVIDER_LIVE = "provider-live"


_RENDER_EVIDENCE = frozenset({"synthetic-bytes", "host-tts", "provider-response"})
_TRANSCRIPT_EVIDENCE = frozenset({"script-derived", "provider-asr"})
_PUBLICATION_SCOPES = frozenset({"filesystem", "object-storage", "external"})
_PROVIDER_FIELDS = ("provider", "model", "request_id")
_COST_FIELDS = ("currency", "estimated", "reconciled")


def validate_execution_evidence(record: Mapping[str, object]) -> dict[str, object]:
    """Validate and return an execution-evidence record without external calls."""
    if not isinstance(record, Mapping):
        raise ValueError("execution evidence must be an object")
    validated = dict(record)
    _require_equal(validated, "schema_version", "1.0")
    _require_member(validated, "execution_mode", ExecutionMode)
    _require_member(validated, "render_evidence", _RENDER_EVIDENCE)
    _require_member(validated, "transcript_evidence", _TRANSCRIPT_EVIDENCE)
    _require_member(validated, "publication_scope", _PUBLICATION_SCOPES)
    if type(validated.get("live_eligible")) is not bool:
        raise ValueError("live_eligible must be a boolean")

    for field in ("renderer", "transcriber"):
        if field in validated:
            _validate_provider_metadata(field, validated[field])

    if validated["live_eligible"]:
        _validate_live_eligibility(validated)
    return validated


def _require_equal(record: Mapping[str, object], field: str, expected: str) -> None:
    if record.get(field) != expected:
        raise ValueError(f"{field} must be {expected!r}")


def _require_member(
    record: Mapping[str, object], field: str, values: type[StrEnum] | frozenset[str]
) -> None:
    value = record.get(field)
    if value not in values:
        raise ValueError(f"{field} has an unknown value")


def _validate_provider_metadata(field: str, value: object) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    for name in _PROVIDER_FIELDS:
        if not isinstance(value.get(name), str) or not value[name]:
            raise ValueError(f"{field}.{name} must be a non-empty string")


def _validate_live_eligibility(record: Mapping[str, object]) -> None:
    _require_equal(record, "execution_mode", ExecutionMode.LIVE_PROVIDER.value)
    _require_equal(record, "render_evidence", "provider-response")
    _require_equal(record, "transcript_evidence", "provider-asr")
    if record.get("consent_valid") is not True:
        raise ValueError("live_eligible evidence requires valid consent")
    if record.get("critical_token_accuracy") != 1.0:
        raise ValueError(
            "live_eligible evidence requires critical_token_accuracy of 1.0"
        )
    _validate_provider_metadata("renderer", record.get("renderer"))
    _validate_provider_metadata("transcriber", record.get("transcriber"))
    _validate_cost_evidence(record.get("cost_evidence"))


def _validate_cost_evidence(value: object) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("cost_evidence must be an object")
    currency = value.get("currency")
    if not isinstance(currency, str) or not currency:
        raise ValueError("cost_evidence.currency must be a non-empty string")
    for field in _COST_FIELDS[1:]:
        amount = value.get(field)
        if type(amount) not in (int, float):
            raise ValueError(
                f"cost_evidence.{field} must be a finite non-negative number"
            )
        numeric_amount = float(cast(int | float, amount))
        if not math.isfinite(numeric_amount) or numeric_amount < 0:
            raise ValueError(
                f"cost_evidence.{field} must be a finite non-negative number"
            )
