"""Acceptance tests for explicit, secret-safe provider runtime settings."""

import json
from decimal import Decimal

import pytest
from pytest_bdd import given, scenarios, then, when

from poddown.providers.settings import ProviderRuntimeSettings

scenarios("../features/provider_runtime_settings.feature")


def _route_record(*, mode: str) -> dict[str, object]:
    if mode == "live-provider":
        renderer: dict[str, object] = {
            "provider": "elevenlabs",
            "model": "eleven-multilingual-v2",
            "voice_asset_id": "voice-public-1",
            "required_capabilities": ["wav", "voice-pinning"],
            "secret_ref": "env://ELEVENLABS_API_KEY",
        }
        transcriber: dict[str, object] = {
            "provider": "openai",
            "model": "whisper-1",
            "required_capabilities": ["timestamps"],
            "secret_ref": "env://OPENAI_API_KEY",
        }
        return {
            "route_id": "live-primary",
            "mode": mode,
            "renderer": renderer,
            "transcriber": transcriber,
            "fallbacks": [],
            "pricing_version": "2026-08-14",
            "max_request_cost": "0.25",
            "max_episode_cost": "5.00",
        }
    binding = {
        "provider": "local",
        "model": "local-deterministic-v1",
        "required_capabilities": ["wav", "timestamps"],
    }
    return {
        "route_id": "local-route",
        "mode": mode,
        "renderer": binding,
        "transcriber": binding.copy(),
        "fallbacks": [],
        "pricing_version": "local-v1",
        "max_request_cost": "0",
        "max_episode_cost": "0",
    }


def _environment(
    route: dict[str, object], *, live_enabled: str = "1"
) -> dict[str, str]:
    return {
        "PODDOWN_PROVIDER_ROUTE_JSON": json.dumps(route),
        "PODDOWN_PROVIDER_TIMEOUT_SECONDS": "30",
        "PODDOWN_PROVIDER_LIVE_ENABLED": live_enabled,
        "PODDOWN_PROVIDER_ENDPOINT": "https://providers.example.test/v1"
        if route["mode"] == "live-provider"
        else "",
    }


@given("a valid live provider route record")
def valid_live_provider_route(context) -> None:
    """Provide a complete live route without a credential value."""
    context.values["environment"] = _environment(_route_record(mode="live-provider"))


@given("a valid deterministic-local route record")
def valid_local_provider_route(context) -> None:
    """Provide a credential-free deterministic route."""
    context.values["environment"] = _environment(
        _route_record(mode="deterministic-local"), live_enabled="0"
    )


@when("live provider runtime settings are loaded")
def load_live_settings(context) -> None:
    """Load settings through the environment boundary."""
    context.values["settings"] = ProviderRuntimeSettings.from_environment(
        context.values["environment"]
    )


@when("local provider runtime settings are loaded")
def load_local_settings(context) -> None:
    """Load local settings through the same environment boundary."""
    context.values["settings"] = ProviderRuntimeSettings.from_environment(
        context.values["environment"]
    )


@when("live provider runtime settings are loaded without opt-in")
def load_live_settings_without_opt_in(context) -> None:
    """Capture the fail-closed error before a provider transport can run."""
    environment = dict(context.values["environment"])
    environment["PODDOWN_PROVIDER_LIVE_ENABLED"] = "0"
    with pytest.raises(ValueError) as error:
        ProviderRuntimeSettings.from_environment(environment)
    context.values["error"] = error.value


@then("the route remains live-provider and explicitly enabled")
def live_settings_are_explicit(context) -> None:
    """Require live mode, opt-in, endpoint, and bounded costs."""
    settings = context.values["settings"]
    assert settings.route.mode == "live-provider"
    assert settings.live_enabled is True
    assert settings.endpoint == "https://providers.example.test/v1"
    assert settings.route.max_request_cost == Decimal("0.25")


@then("the route remains offline with zero cost")
def local_settings_are_offline(context) -> None:
    """Require local mode to remain credential-free and cost-free."""
    settings = context.values["settings"]
    assert settings.route.mode == "deterministic-local"
    assert settings.live_enabled is False
    assert settings.endpoint is None
    assert settings.route.max_episode_cost == Decimal("0")


@then("provider dispatch is rejected before any request")
def provider_dispatch_is_rejected(context) -> None:
    """Require an explicit opt-in failure with no transport involved."""
    assert "enablement" in str(context.values["error"])
