"""Contract coverage for the execution-evidence JSON schema."""

import json
from pathlib import Path

from poddown.evidence import EvidenceKind, ExecutionMode


def test_execution_evidence_schema_freezes_production_closure_vocabulary() -> None:
    schema_path = Path(
        "specs/003-durable-audio-production/contracts/execution-evidence.schema.json"
    )
    schema = json.loads(schema_path.read_text())
    properties = schema["properties"]

    assert properties["schema_version"]["const"] == "1.0"
    assert properties["execution_mode"]["enum"] == [
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
        "execution_mode",
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
    assert [mode.value for mode in ExecutionMode] == properties["execution_mode"][
        "enum"
    ]
    assert [kind.value for kind in EvidenceKind] == [
        "synthetic",
        "host-local",
        "provider-live",
    ]
