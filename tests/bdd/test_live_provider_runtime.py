"""BDD coverage for guarded live provider runtime construction."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from pytest_bdd import given, scenarios, then, when

from poddown.providers.runtime import LiveProviderRuntime

scenarios("../features/live_provider_runtime.feature")


def _environment() -> dict[str, str]:
    route = {
        "route_id": "live-primary",
        "mode": "live-provider",
        "renderer": {
            "provider": "elevenlabs",
            "model": "eleven-multilingual-v2",
            "voice_asset_id": "voice-route-default",
            "required_capabilities": ["wav", "voice-pinning"],
            "secret_ref": "env://ELEVENLABS_API_KEY",
        },
        "transcriber": {
            "provider": "openai",
            "model": "whisper-1",
            "required_capabilities": ["timestamps"],
            "secret_ref": "env://OPENAI_API_KEY",
        },
        "fallbacks": [],
        "pricing_version": "2026-08-14",
        "max_request_cost": "0.25",
        "max_episode_cost": "5.00",
    }
    return {
        "PODDOWN_PROVIDER_ROUTE_JSON": json.dumps(route),
        "PODDOWN_PROVIDER_TIMEOUT_SECONDS": "30",
        "PODDOWN_PROVIDER_LIVE_ENABLED": "1",
        "PODDOWN_PROVIDER_ENDPOINT": "https://providers.example.test/v1",
        "ELEVENLABS_API_KEY": "test-elevenlabs-secret",
        "OPENAI_API_KEY": "test-openai-secret",
        "PODDOWN_LIVE_VOICE_MAP_JSON": json.dumps(
            {
                "demo-reference-host-v1": "voice-host",
                "demo-reference-analyst-v1": "voice-analyst",
            }
        ),
        "PODDOWN_LIVE_CONSENT_JSON": json.dumps(
            {
                "voice-host": "consent-host-2026",
                "voice-analyst": "consent-analyst-2026",
            }
        ),
        "PODDOWN_ELEVENLABS_COST_PER_CHARACTER": "0.00001",
        "PODDOWN_OPENAI_COST_PER_AUDIO_SECOND": "0.0001",
        "PODDOWN_OPENAI_REASONING_MODEL": "gpt-4.1-mini",
        "PODDOWN_OPENAI_COST_PER_REASONING_INPUT_TOKEN": "0.000001",
        "PODDOWN_OPENAI_COST_PER_REASONING_OUTPUT_TOKEN": "0.000004",
    }


@given("a complete live provider runtime environment")
def complete_environment(context) -> None:
    context.values["environment"] = _environment()


@given("a live provider runtime environment without consent evidence")
def environment_without_consent(context) -> None:
    environment = _environment()
    environment.pop("PODDOWN_LIVE_CONSENT_JSON")
    context.values["environment"] = environment


@when("the live provider runtime is constructed")
def construct_runtime(context) -> None:
    context.values["runtime"] = LiveProviderRuntime.from_environment(
        context.values["environment"]
    )


@when("I attempt to construct the live provider runtime")
def attempt_construct_runtime(context) -> None:
    with pytest.raises(ValueError) as error:
        LiveProviderRuntime.from_environment(context.values["environment"])
    context.values["error"] = error.value


@when("live provider enablement is removed")
def remove_enablement(context) -> None:
    environment = dict(context.values["environment"])
    environment["PODDOWN_PROVIDER_LIVE_ENABLED"] = "0"
    with pytest.raises(ValueError) as error:
        LiveProviderRuntime.from_environment(environment)
    context.values["error"] = error.value


@then("the runtime exposes the pinned live route and mapped voices")
def runtime_is_explicit(context) -> None:
    runtime = context.values["runtime"]
    assert runtime.settings.route.route_id == "live-primary"
    assert runtime.settings.route.mode == "live-provider"
    assert runtime.render_binding.voice_asset_ids == {
        "demo-reference-host-v1": "voice-host",
        "demo-reference-analyst-v1": "voice-analyst",
    }
    assert runtime.render_binding.consents["voice-host"].evidence_id == (
        "consent-host-2026"
    )
    assert runtime.render_estimated_cost == Decimal("0.25")
    assert runtime.reasoning is not None


@then("live runtime construction fails before provider dispatch")
def runtime_fails_closed(context) -> None:
    assert (
        "consent" in str(context.values["error"]).casefold()
        or "enable" in str(context.values["error"]).casefold()
    )
