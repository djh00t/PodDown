"""Unit coverage for execution-evidence validation."""

from copy import deepcopy

import pytest

from poddown.evidence import EvidenceKind, ExecutionMode, validate_execution_evidence


def test_accepts_explicitly_labelled_deterministic_and_host_local_evidence() -> None:
    deterministic = {
        "schema_version": "1.0",
        "mode": "deterministic-local",
        "render_evidence": "synthetic-bytes",
        "transcript_evidence": "script-derived",
        "publication_scope": "filesystem",
        "live_eligible": False,
    }
    host_local = {
        **deterministic,
        "mode": "host-local",
        "render_evidence": "host-tts",
    }

    assert validate_execution_evidence(deterministic) == deterministic
    assert validate_execution_evidence(host_local) == host_local
    assert ExecutionMode.DETERMINISTIC_LOCAL.value == "deterministic-local"
    assert EvidenceKind.HOST_LOCAL.value == "host-local"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("mode", "sandbox", "mode"),
        ("render_evidence", "recorded-file", "render_evidence"),
        ("transcript_evidence", "manual", "transcript_evidence"),
    ],
)
def test_rejects_unknown_execution_evidence_values(
    field: str, value: str, message: str
) -> None:
    record = {
        "schema_version": "1.0",
        "mode": "deterministic-local",
        "render_evidence": "synthetic-bytes",
        "transcript_evidence": "script-derived",
        "publication_scope": "filesystem",
        "live_eligible": False,
    }
    record[field] = value

    with pytest.raises(ValueError, match=message):
        validate_execution_evidence(record)


def test_rejects_malformed_provider_metadata() -> None:
    record = {
        "schema_version": "1.0",
        "mode": "host-local",
        "render_evidence": "host-tts",
        "transcript_evidence": "script-derived",
        "publication_scope": "filesystem",
        "live_eligible": False,
        "renderer": {"provider": "macos", "model": "say"},
    }

    with pytest.raises(ValueError, match="renderer.request_ids"):
        validate_execution_evidence(record)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("unexpected", "value", "unknown field"),
        ("consent_valid", "yes", "consent_valid"),
        ("critical_token_accuracy", True, "critical_token_accuracy"),
        ("critical_token_accuracy", float("nan"), "critical_token_accuracy"),
        (
            "cost_evidence",
            {"currency": "USD", "estimated": 0, "reconciled": True},
            "cost_evidence",
        ),
    ],
)
def test_rejects_unknown_or_malformed_optional_evidence(
    field: str, value: object, message: str
) -> None:
    record = {
        "schema_version": "1.0",
        "mode": "deterministic-local",
        "render_evidence": "synthetic-bytes",
        "transcript_evidence": "script-derived",
        "publication_scope": "filesystem",
        "live_eligible": False,
    }
    record[field] = value

    with pytest.raises(ValueError, match=message):
        validate_execution_evidence(record)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "renderer",
            {
                "provider": "macos",
                "model": "say",
                "request_ids": ["1"],
                "extra": "no",
            },
            "renderer",
        ),
        (
            "cost_evidence",
            {"currency": "USD", "estimated": 0, "reconciled": 0, "extra": 1},
            "cost_evidence",
        ),
    ],
)
def test_rejects_unknown_nested_evidence_fields(
    field: str, value: object, message: str
) -> None:
    record = {
        "schema_version": "1.0",
        "mode": "deterministic-local",
        "render_evidence": "synthetic-bytes",
        "transcript_evidence": "script-derived",
        "publication_scope": "filesystem",
        "live_eligible": False,
    }
    record[field] = value

    with pytest.raises(ValueError, match=message):
        validate_execution_evidence(record)


def test_rejects_live_eligible_record_without_complete_live_evidence() -> None:
    record = {
        "schema_version": "1.0",
        "mode": "live-provider",
        "render_evidence": "provider-response",
        "transcript_evidence": "provider-asr",
        "publication_scope": "object-storage",
        "live_eligible": True,
        "renderer": {
            "provider": "elevenlabs",
            "model": "eleven_multilingual_v2",
            "request_ids": ["render-1"],
        },
        "transcriber": {
            "provider": "openai",
            "model": "gpt-4o-transcribe",
            "request_ids": ["asr-1"],
        },
        "consent_valid": True,
        "critical_token_accuracy": 1.0,
    }
    incomplete = deepcopy(record)
    incomplete["cost_evidence"] = {"currency": "USD"}

    with pytest.raises(ValueError, match="cost_evidence"):
        validate_execution_evidence(incomplete)


def test_accepts_complete_live_provider_evidence_with_request_id_arrays() -> None:
    record = {
        "schema_version": "1.0",
        "mode": "live-provider",
        "render_evidence": "provider-response",
        "transcript_evidence": "provider-asr",
        "publication_scope": "external",
        "live_eligible": True,
        "renderer": {
            "provider": "elevenlabs",
            "model": "eleven_multilingual_v2",
            "request_ids": ["render-1", "render-2"],
        },
        "transcriber": {
            "provider": "openai",
            "model": "gpt-4o-transcribe",
            "request_ids": ["asr-1"],
        },
        "consent_valid": True,
        "critical_token_accuracy": 1.0,
        "cost_evidence": {"currency": "USD", "estimated": 1.5, "reconciled": 1.5},
    }

    assert validate_execution_evidence(record) == record


def test_rejects_live_eligible_evidence_with_boolean_token_accuracy() -> None:
    record = {
        "schema_version": "1.0",
        "mode": "live-provider",
        "render_evidence": "provider-response",
        "transcript_evidence": "provider-asr",
        "publication_scope": "object-storage",
        "live_eligible": True,
        "renderer": {
            "provider": "elevenlabs",
            "model": "eleven_multilingual_v2",
            "request_ids": ["render-1"],
        },
        "transcriber": {
            "provider": "openai",
            "model": "gpt-4o-transcribe",
            "request_ids": ["asr-1"],
        },
        "consent_valid": True,
        "critical_token_accuracy": True,
        "cost_evidence": {"currency": "USD", "estimated": 1.5, "reconciled": 1.5},
    }

    with pytest.raises(ValueError, match="critical_token_accuracy"):
        validate_execution_evidence(record)
