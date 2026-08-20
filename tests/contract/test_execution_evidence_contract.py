"""Contract coverage for the execution-evidence JSON schema."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from poddown.evidence import EvidenceKind, ExecutionMode


def test_execution_evidence_schema_freezes_production_closure_vocabulary() -> None:
    schema_path = Path(
        "specs/003-durable-audio-production/contracts/execution-evidence.schema.json"
    )
    schema = json.loads(schema_path.read_text())
    properties = schema["properties"]

    assert properties["schema_version"]["const"] == "1.0"
    assert properties["mode"]["enum"] == [
        "deterministic-local",
        "host-local",
        "live-provider",
    ]
    assert properties["render_evidence"]["enum"] == [
        "synthetic-bytes",
        "host-tts",
        "provider-response",
    ]
    assert properties["transcript_evidence"]["enum"] == [
        "script-derived",
        "provider-asr",
    ]
    assert properties["publication_scope"]["enum"] == [
        "filesystem",
        "object-storage",
        "external",
    ]
    assert set(properties) == {
        "schema_version",
        "mode",
        "render_evidence",
        "transcript_evidence",
        "publication_scope",
        "live_eligible",
        "renderer",
        "transcriber",
        "consent_valid",
        "critical_token_accuracy",
        "cost_evidence",
    }
    assert [mode.value for mode in ExecutionMode] == properties["mode"]["enum"]
    provider_metadata = schema["$defs"]["provider_metadata"]
    assert provider_metadata["required"] == ["provider", "model", "request_ids"]
    assert provider_metadata["properties"]["request_ids"] == {
        "type": "array",
        "minItems": 1,
        "items": {"type": "string", "minLength": 1},
    }
    assert [kind.value for kind in EvidenceKind] == [
        "synthetic",
        "host-local",
        "provider-live",
    ]


@pytest.mark.parametrize(
    "record",
    [
        {
            "schema_version": "1.0",
            "mode": "deterministic-local",
            "render_evidence": "provider-response",
            "transcript_evidence": "script-derived",
            "publication_scope": "filesystem",
            "live_eligible": False,
        },
        {
            "schema_version": "1.0",
            "mode": "host-local",
            "render_evidence": "host-tts",
            "transcript_evidence": "script-derived",
            "publication_scope": "external",
            "live_eligible": False,
        },
        {
            "schema_version": "1.0",
            "mode": "live-provider",
            "render_evidence": "provider-response",
            "transcript_evidence": "provider-asr",
            "publication_scope": "object-storage",
            "live_eligible": True,
            "renderer": {
                "provider": "host-local",
                "model": "host-local-tts-v1",
                "request_ids": ["render-1"],
            },
            "transcriber": {
                "provider": "openai",
                "model": "gpt-4o-transcribe",
                "request_ids": ["asr-1"],
            },
            "consent_valid": True,
            "critical_token_accuracy": 1.0,
            "cost_evidence": {
                "currency": "USD",
                "estimated": 1.0,
                "reconciled": 1.0,
            },
        },
    ],
)
def test_schema_rejects_mode_mismatched_and_local_live_provider_evidence(
    record: dict[str, object],
) -> None:
    schema = json.loads(
        Path(
            "specs/003-durable-audio-production/contracts/execution-evidence.schema.json"
        ).read_text()
    )

    errors = list(Draft202012Validator(schema).iter_errors(record))

    assert errors
