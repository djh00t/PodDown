"""Unit contracts for fail-closed provider runtime settings."""

import json
from decimal import Decimal

import pytest

from poddown.providers.settings import ProviderRuntimeSettings


def _route_record(
    *,
    mode: str = "live-provider",
    renderer_model: str = "eleven-multilingual-v2",
    renderer_voice_asset_id: str | None = "voice-public-1",
    renderer_capabilities: list[str] | None = None,
    transcriber_model: str = "whisper-1",
    transcriber_voice_asset_id: str | None = None,
    max_request_cost: str = "0.25",
    max_episode_cost: str = "5.00",
) -> dict[str, object]:
    if mode == "live-provider":
        renderer: dict[str, object] = {
            "provider": "elevenlabs",
            "model": renderer_model,
            "required_capabilities": renderer_capabilities or ["wav", "voice-pinning"],
            "secret_ref": "env://ELEVENLABS_API_KEY",
        }
        transcriber: dict[str, object] = {
            "provider": "openai",
            "model": transcriber_model,
            "required_capabilities": ["timestamps"],
            "secret_ref": "env://OPENAI_API_KEY",
        }
        if renderer_voice_asset_id is not None:
            renderer["voice_asset_id"] = renderer_voice_asset_id
        if transcriber_voice_asset_id is not None:
            transcriber["voice_asset_id"] = transcriber_voice_asset_id
    else:
        binding = {
            "provider": "local",
            "model": "local-deterministic-v1",
            "required_capabilities": ["wav", "timestamps"],
        }
        renderer = binding
        transcriber = binding.copy()
    return {
        "route_id": "provider-route",
        "mode": mode,
        "renderer": renderer,
        "transcriber": transcriber,
        "fallbacks": [],
        "pricing_version": "2026-08-14",
        "max_request_cost": max_request_cost if mode == "live-provider" else "0",
        "max_episode_cost": max_episode_cost if mode == "live-provider" else "0",
    }


def _environment(route: dict[str, object], **overrides: str) -> dict[str, str]:
    environment = {
        "PODDOWN_PROVIDER_ROUTE_JSON": json.dumps(route),
        "PODDOWN_PROVIDER_TIMEOUT_SECONDS": "30",
        "PODDOWN_PROVIDER_LIVE_ENABLED": "1",
        "PODDOWN_PROVIDER_ENDPOINT": "https://providers.example.test/v1",
    }
    if route["mode"] != "live-provider":
        environment["PODDOWN_PROVIDER_LIVE_ENABLED"] = "0"
        environment["PODDOWN_PROVIDER_ENDPOINT"] = ""
    environment.update(overrides)
    return environment


def test_live_settings_require_opt_in_endpoint_and_role_metadata() -> None:
    """A live route must name the approved renderer and transcriber roles."""
    settings = ProviderRuntimeSettings.from_environment(_environment(_route_record()))

    assert settings.endpoint == "https://providers.example.test/v1"
    assert settings.timeout_seconds == 30.0
    assert settings.route.max_request_cost == Decimal("0.25")
    assert settings.to_record()["live_enabled"] is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"PODDOWN_PROVIDER_LIVE_ENABLED": "0"},
        {"PODDOWN_PROVIDER_ENDPOINT": ""},
        {"PODDOWN_PROVIDER_ENDPOINT": "http://providers.example.test"},
        {"PODDOWN_PROVIDER_ENDPOINT": "https://user:pass@providers.example.test"},
        {"PODDOWN_PROVIDER_TIMEOUT_SECONDS": "0"},
        {"PODDOWN_PROVIDER_TIMEOUT_SECONDS": "301"},
        {"PODDOWN_PROVIDER_TIMEOUT_SECONDS": "nan"},
    ],
)
def test_live_settings_reject_missing_or_unsafe_dispatch_controls(
    overrides: dict[str, str],
) -> None:
    """Opt-in, endpoint, and timeout controls fail closed before dispatch."""
    with pytest.raises(ValueError):
        ProviderRuntimeSettings.from_environment(
            _environment(_route_record(), **overrides)
        )


@pytest.mark.parametrize(
    "route",
    [
        _route_record(renderer_model="unsupported-model"),
        _route_record(renderer_voice_asset_id=None),
        _route_record(renderer_capabilities=["wav"]),
        _route_record(transcriber_model="gpt-4o-mini-tts"),
        _route_record(transcriber_voice_asset_id="voice-public-1"),
        _route_record(max_request_cost="0", max_episode_cost="5.00"),
    ],
)
def test_live_settings_reject_invalid_role_model_voice_or_budget(
    route: dict[str, object],
) -> None:
    """Unsupported live provider combinations cannot reach a transport."""
    with pytest.raises(ValueError):
        ProviderRuntimeSettings.from_environment(_environment(route))


def test_local_settings_remain_zero_cost_and_without_endpoint() -> None:
    """Local routes preserve their offline identity and bounded cost policy."""
    settings = ProviderRuntimeSettings.from_environment(
        _environment(_route_record(mode="deterministic-local"))
    )

    assert settings.route.mode == "deterministic-local"
    assert settings.route.max_request_cost == Decimal("0")
    assert settings.route.max_episode_cost == Decimal("0")
    assert settings.endpoint is None
    assert settings.live_enabled is False


def test_settings_do_not_serialize_raw_credentials() -> None:
    """Runtime settings contain references and controls, never credential values."""
    raw_secret = "raw-provider-credential"
    settings = ProviderRuntimeSettings.from_environment(
        _environment(_route_record(), OPENAI_API_KEY=raw_secret)
    )

    assert raw_secret not in repr(settings)
    assert raw_secret not in json.dumps(settings.to_record())
