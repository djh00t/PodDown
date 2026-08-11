"""Executable schema-plus-normalizer contract checks for provider-route records."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from poddown.provider_routes import ProviderRoute


def _schema() -> dict[str, object]:
    schema_path = (
        Path(__file__).parents[2]
        / "specs/003-durable-audio-production/contracts/provider-route.schema.json"
    )
    return json.loads(schema_path.read_text())


def _record(mode: str, provider: str, secret_ref: str | None) -> dict[str, object]:
    binding: dict[str, object] = {
        "provider": provider,
        "model": "model-v1",
        "required_capabilities": ["wav"],
    }
    if secret_ref is not None:
        binding["secret_ref"] = secret_ref
    return {
        "route_id": f"{mode}-route",
        "mode": mode,
        "renderer": binding,
        "transcriber": binding.copy(),
        "fallbacks": [],
        "pricing_version": "v1",
        "max_request_cost": "1.00",
        "max_episode_cost": "1.00",
    }


@pytest.mark.parametrize(
    ("mode", "provider", "secret_ref"),
    [
        ("deterministic-local", "local", None),
        ("host-local", "host-local", None),
        ("live-provider", "openai", "env://OPENAI_API_KEY"),
    ],
)
def test_schema_records_round_trip_through_the_package_owned_normalizer(
    mode: str, provider: str, secret_ref: str | None
) -> None:
    """Each route mode has one exact JSON record normalization path."""
    record = _record(mode, provider, secret_ref)

    assert ProviderRoute.from_record(record).to_record() == record


@pytest.mark.parametrize(
    "mutate",
    [
        lambda record: record.update(max_request_cost="1E+2"),
        lambda record: record.update(max_request_cost="01.00"),
        lambda record: record["renderer"].update(  # type: ignore[union-attr]
            required_capabilities=["wav", "wav"]
        ),
        lambda record: record["renderer"].update(  # type: ignore[union-attr]
            required_capabilities=["wav", 1]
        ),
        lambda record: record["renderer"].update(  # type: ignore[union-attr]
            required_capabilities=[""]
        ),
        lambda record: record.update(route_id="  "),
        lambda record: record["renderer"].update(model="  "),  # type: ignore[union-attr]
        lambda record: record.update(pricing_version="\t"),
        lambda record: record["renderer"].update(voice_asset_id="\n"),  # type: ignore[union-attr]
        lambda record: record["renderer"].update(secret_ref="env://\n"),  # type: ignore[union-attr]
        lambda record: record.update(max_request_cost="1.01"),
        lambda record: record.update(mode="host-local"),
        lambda record: (
            record.update(mode="deterministic-local"),
            record["renderer"].update(  # type: ignore[union-attr]
                provider="local", secret_ref="env://LOCAL_SECRET"
            ),
            record["transcriber"].update(  # type: ignore[union-attr]
                provider="local", secret_ref="env://LOCAL_SECRET"
            ),
        ),
        lambda record: record["renderer"].pop("secret_ref"),  # type: ignore[union-attr]
    ],
)
def test_schema_invalid_records_are_rejected_by_the_package_owned_normalizer(
    mutate: Callable[[dict[str, object]], object],
) -> None:
    """Structural and semantic JSON record failures share one executable boundary."""
    record = _record("live-provider", "openai", "env://OPENAI_API_KEY")
    mutate(record)
    if record["max_request_cost"] == "1.01":
        record["max_episode_cost"] = "1.00"

    with pytest.raises(ValueError):
        ProviderRoute.from_record(record)


def test_schema_documents_the_complete_schema_plus_normalizer_boundary() -> None:
    """The schema points JSON record validation at its package-owned normalizer."""
    schema = _schema()

    assert schema["x-poddown-validation-boundary"] == {
        "description": (
            "JSON Schema validates route-record structure; "
            "ProviderRoute.from_record is the package-owned normalizer and "
            "enforces max_episode_cost >= max_request_cost."
        ),
        "normalizer": "ProviderRoute.from_record",
        "normalizer_enforces": ["max_episode_cost >= max_request_cost"],
    }
    assert "x-poddown-cost-ceiling" not in schema


def test_schema_declares_the_same_lexical_constraints_as_the_normalizer() -> None:
    """Schema lexical declarations match normalizer-backed JSON record fixtures."""
    schema = _schema()
    definitions = schema["$defs"]
    properties = schema["properties"]
    binding = definitions["providerBinding"]

    assert definitions["nonWhitespaceString"] == {
        "type": "string",
        "pattern": ".*\\S.*",
    }
    assert definitions["decimal"] == {
        "type": "string",
        "pattern": "^(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?$",
    }
    assert properties["route_id"] == {"$ref": "#/$defs/nonWhitespaceString"}
    assert properties["pricing_version"] == {"$ref": "#/$defs/nonWhitespaceString"}
    assert binding["properties"]["model"] == {"$ref": "#/$defs/nonWhitespaceString"}
    assert binding["properties"]["voice_asset_id"] == {
        "type": ["string", "null"],
        "pattern": ".*\\S.*",
    }
    assert binding["properties"]["required_capabilities"] == {
        "type": "array",
        "items": {"$ref": "#/$defs/nonWhitespaceString"},
        "uniqueItems": True,
    }
