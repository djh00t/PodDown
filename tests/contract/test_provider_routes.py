"""Contract-shape checks for the provider-route JSON schema."""

import json
from pathlib import Path


def test_provider_route_schema_declares_closed_route_and_binding_contracts() -> None:
    """The schema exposes the same closed route record shape as the Python model."""
    schema_path = (
        Path(__file__).parents[2]
        / "specs/003-durable-audio-production/contracts/provider-route.schema.json"
    )
    schema = json.loads(schema_path.read_text())

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["mode"]["enum"] == [
        "deterministic-local",
        "host-local",
        "live-provider",
    ]
    binding = schema["$defs"]["providerBinding"]
    assert binding["additionalProperties"] is False
    assert binding["properties"]["provider"]["enum"] == [
        "local-system-tts-demo",
        "elevenlabs",
        "openai",
    ]
    assert binding["properties"]["secret_ref"]["type"] == ["string", "null"]
