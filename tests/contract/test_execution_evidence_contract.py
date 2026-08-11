"""Contract coverage for the execution-evidence JSON schema."""

import json
from pathlib import Path


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
