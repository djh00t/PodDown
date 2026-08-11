"""Executable JSON contract checks for provider-route records."""

import json
import re
from collections.abc import Mapping
from pathlib import Path

import pytest

from poddown.provider_routes import ProviderRoute


def _schema() -> dict[str, object]:
    schema_path = (
        Path(__file__).parents[2]
        / "specs/003-durable-audio-production/contracts/provider-route.schema.json"
    )
    return json.loads(schema_path.read_text())


def _schema_rejection(schema: Mapping[str, object], record: object) -> str | None:
    """Return the schema-rule violation for the focused record subset, if any."""
    if not isinstance(record, dict) or set(record) != set(schema["required"]):
        return "route fields"
    properties = schema["properties"]
    if not isinstance(properties, dict):
        raise AssertionError("route properties must be an object")
    if record["mode"] not in properties["mode"]["enum"]:
        return "mode"
    for field in ("route_id", "pricing_version"):
        value = record[field]
        if not isinstance(value, str) or not value:
            return field
    decimal = schema["$defs"]["decimal"]
    for field in ("max_request_cost", "max_episode_cost"):
        value = record[field]
        if not isinstance(value, str) or re.fullmatch(decimal["pattern"], value) is None:
            return field
    mode_bindings = {
        rule["if"]["properties"]["mode"]["const"]: rule["then"]["properties"]
        for rule in schema["allOf"]
    }
    bindings = mode_bindings[record["mode"]]
    for field in ("renderer", "transcriber"):
        rejection = _binding_rejection(schema, record[field], bindings[field]["$ref"])
        if rejection is not None:
            return f"{field}.{rejection}"
    fallbacks = record["fallbacks"]
    if not isinstance(fallbacks, list):
        return "fallbacks"
    fallback_ref = bindings["fallbacks"]["items"]["$ref"]
    for fallback in fallbacks:
        rejection = _binding_rejection(schema, fallback, fallback_ref)
        if rejection is not None:
            return f"fallbacks.{rejection}"
    return None


def _binding_rejection(
    schema: Mapping[str, object], binding: object, binding_ref: str
) -> str | None:
    if not isinstance(binding, dict):
        return "object"
    binding_schema = schema["$defs"]["providerBinding"]
    if set(binding) - set(binding_schema["properties"]) or not set(
        binding_schema["required"]
    ) <= set(binding):
        return "fields"
    provider = binding["provider"]
    if provider not in binding_schema["properties"]["provider"]["enum"]:
        return "provider"
    specialized = schema["$defs"][binding_ref.rsplit("/", 1)[-1]]
    allowed_providers = specialized["allOf"][1]["properties"]["provider"]
    if provider not in allowed_providers.get("enum", [allowed_providers.get("const")]):
        return "provider"
    for field in ("model",):
        value = binding[field]
        if not isinstance(value, str) or not value:
            return field
    capabilities = binding["required_capabilities"]
    if (
        not isinstance(capabilities, list)
        or len(capabilities) != len(set(capabilities))
        or not all(isinstance(item, str) and item for item in capabilities)
    ):
        return "required_capabilities"
    secret_ref = binding.get("secret_ref")
    if provider in {"local", "host-local"}:
        if secret_ref is not None:
            return "secret_ref"
    elif (
        not isinstance(secret_ref, str)
        or re.fullmatch(binding_schema["properties"]["secret_ref"]["pattern"], secret_ref)
        is None
    ):
        return "secret_ref"
    return None


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
def test_schema_valid_records_round_trip_through_the_fail_closed_normalizer(
    mode: str, provider: str, secret_ref: str | None
) -> None:
    """Each schema mode has a deterministic, exact normalizer round-trip."""
    record = _record(mode, provider, secret_ref)

    assert _schema_rejection(_schema(), record) is None
    assert ProviderRoute.from_record(record).to_record() == record


@pytest.mark.parametrize(
    ("mutator", "expected_rejection"),
    [
        (lambda record: record["renderer"].update(required_capabilities=["wav", "wav"]), "renderer.required_capabilities"),
        (lambda record: record["renderer"].update(required_capabilities=["wav", 1]), "renderer.required_capabilities"),
        (lambda record: record.update(max_request_cost="1E+2"), "max_request_cost"),
        (lambda record: record.update(max_request_cost="01.00"), "max_request_cost"),
        (lambda record: record.update(mode="host-local"), "renderer.provider"),
        (lambda record: record["renderer"].update(secret_ref="env://LOCAL_SECRET"), "renderer.secret_ref"),
    ],
)
def test_schema_rejections_are_also_rejected_by_the_record_parser(
    mutator: object, expected_rejection: str
) -> None:
    """Parser rejection agrees with schema-visible lexical and binding constraints."""
    record = _record("deterministic-local", "local", None)
    mutator(record)  # type: ignore[operator]

    assert _schema_rejection(_schema(), record) == expected_rejection
    with pytest.raises(ValueError):
        ProviderRoute.from_record(record)


def test_schema_normalizer_extension_documents_cost_ceiling_enforced_by_parser() -> None:
    """The cross-field Decimal ceiling is explicit outside standard schema keywords."""
    schema = _schema()

    assert schema["x-poddown-validation-boundary"] == {
        "description": "JSON Schema validates record structure; the normalizer also enforces max_episode_cost >= max_request_cost.",
        "normalizer": "poddown.provider_routes.ProviderRoute.from_record",
        "normalizer_enforces": ["max_episode_cost >= max_request_cost"],
    }
    record = _record("host-local", "host-local", None)
    record["max_request_cost"] = "1.01"

    assert _schema_rejection(schema, record) is None
    with pytest.raises(ValueError, match="max_episode_cost"):
        ProviderRoute.from_record(record)
