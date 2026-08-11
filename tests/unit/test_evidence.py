"""Unit coverage for execution-evidence validation."""

from copy import deepcopy

import pytest

from poddown.evidence import EvidenceKind, ExecutionMode, validate_execution_evidence


def test_accepts_explicitly_labelled_deterministic_and_host_local_evidence() -> None:
    deterministic = {
        "schema_version": "1.0",
        "execution_mode": "deterministic-local",
        "render_evidence": "synthetic-bytes",
        "transcript_evidence": "script-derived",
        "publication_scope": "filesystem",
        "live_eligible": False,
    }
    host_local = {
        **deterministic,
        "execution_mode": "host-local",
        "render_evidence": "host-tts",
    }

    assert validate_execution_evidence(deterministic) == deterministic
    assert validate_execution_evidence(host_local) == host_local
    assert ExecutionMode.DETERMINISTIC_LOCAL.value == "deterministic-local"
    assert EvidenceKind.HOST_LOCAL.value == "host-local"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("execution_mode", "sandbox", "execution_mode"),
        ("render_evidence", "recorded-file", "render_evidence"),
        ("transcript_evidence", "manual", "transcript_evidence"),
    ],
)
def test_rejects_unknown_execution_evidence_values(
    field: str, value: str, message: str
) -> None:
    record = {
        "schema_version": "1.0",
        "execution_mode": "deterministic-local",
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
        "execution_mode": "host-local",
        "render_evidence": "host-tts",
        "transcript_evidence": "script-derived",
        "publication_scope": "filesystem",
        "live_eligible": False,
        "renderer": {"provider": "macos", "model": "say"},
    }

    with pytest.raises(ValueError, match="renderer.request_id"):
        validate_execution_evidence(record)


def test_rejects_live_eligible_record_without_complete_live_evidence() -> None:
    record = {
        "schema_version": "1.0",
        "execution_mode": "live-provider",
        "render_evidence": "provider-response",
        "transcript_evidence": "provider-asr",
        "publication_scope": "object-storage",
        "live_eligible": True,
        "renderer": {
            "provider": "elevenlabs",
            "model": "eleven_multilingual_v2",
            "request_id": "render-1",
        },
        "transcriber": {
            "provider": "openai",
            "model": "gpt-4o-transcribe",
            "request_id": "asr-1",
        },
        "consent_valid": True,
        "critical_token_accuracy": 1.0,
    }
    incomplete = deepcopy(record)
    incomplete["cost_evidence"] = {"currency": "USD"}

    with pytest.raises(ValueError, match="cost_evidence"):
        validate_execution_evidence(incomplete)
