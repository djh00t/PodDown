"""Unit contracts for immutable, secret-safe provider route records."""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from poddown.provider_routes import ProviderBinding, ProviderRoute


def test_provider_route_preserves_immutable_bindings_and_cost_boundaries() -> None:
    """Provider routes retain capability sets, tuple fallbacks, and Decimal limits."""
    renderer = ProviderBinding(
        provider="elevenlabs",
        model="eleven-multilingual-v2",
        voice_asset_id="voice-public-1",
        required_capabilities=frozenset({"wav", "voice-pinning"}),
        secret_ref="env://ELEVENLABS_API_KEY",
    )
    route = ProviderRoute(
        route_id="live-primary",
        mode="live-provider",
        renderer=renderer,
        transcriber=ProviderBinding(
            provider="openai",
            model="gpt-4o-transcribe",
            required_capabilities=frozenset({"timestamps"}),
            secret_ref="env://OPENAI_API_KEY",
        ),
        fallbacks=(
            ProviderBinding(
                provider="openai",
                model="gpt-4o-mini-tts",
                required_capabilities=frozenset({"wav"}),
                secret_ref="keychain://poddown/openai",
            ),
        ),
        pricing_version="2026-08-11",
        max_request_cost=Decimal("0.25"),
        max_episode_cost=Decimal("5.00"),
    )

    assert route.fallbacks == (route.fallbacks[0],)
    assert route.renderer.required_capabilities == frozenset({"wav", "voice-pinning"})
    assert route.max_request_cost == Decimal("0.25")
    assert route.max_episode_cost == Decimal("5.00")
    with pytest.raises(FrozenInstanceError):
        route.mode = "host-local"  # type: ignore[misc]


def test_provider_binding_accepts_only_secret_references_not_secret_values() -> None:
    """Route bindings carry references rather than raw provider credentials."""
    with pytest.raises(ValueError, match="secret_ref"):
        ProviderBinding(
            provider="elevenlabs",
            model="eleven-multilingual-v2",
            required_capabilities=frozenset({"wav"}),
            secret_ref="sk-live-raw-secret",
        )


@pytest.mark.parametrize(
    ("mode", "provider"),
    [
        ("deterministic-local", "local"),
        ("host-local", "host-local"),
    ],
)
def test_local_routes_preserve_their_distinct_provider_identity(
    mode: str, provider: str
) -> None:
    """Each local execution mode accepts only its corresponding provider identity."""
    binding = ProviderBinding(
        provider=provider,
        model="local-model",
        required_capabilities=frozenset({"wav"}),
    )

    route = ProviderRoute(
        route_id=f"{mode}-route",
        mode=mode,
        renderer=binding,
        transcriber=binding,
        fallbacks=(),
        pricing_version="local-v1",
        max_request_cost=Decimal("0"),
        max_episode_cost=Decimal("0"),
    )

    assert route.renderer.provider == provider


@pytest.mark.parametrize(
    ("mode", "provider", "secret_ref"),
    [
        ("deterministic-local", "host-local", None),
        ("host-local", "local", None),
        ("live-provider", "local", None),
        ("live-provider", "elevenlabs", None),
        ("host-local", "host-local", "env://LOCAL_SECRET"),
    ],
)
def test_provider_routes_reject_invalid_mode_provider_or_secret_pairings(
    mode: str, provider: str, secret_ref: str | None
) -> None:
    """Route mode and provider identity cannot be substituted for each other."""
    with pytest.raises(ValueError):
        binding = ProviderBinding(
            provider=provider,
            model="model",
            required_capabilities=frozenset({"wav"}),
            secret_ref=secret_ref,
        )
        ProviderRoute(
            route_id="invalid-pairing",
            mode=mode,
            renderer=binding,
            transcriber=binding,
            fallbacks=(),
            pricing_version="v1",
            max_request_cost=Decimal("1"),
            max_episode_cost=Decimal("1"),
        )


def test_route_record_round_trips_decimal_limits_and_rejects_lower_episode_ceiling(
) -> None:
    """JSON records normalize Decimal strings and keep their cost ceiling invariant."""
    record = {
        "route_id": "host-route",
        "mode": "host-local",
        "renderer": {
            "provider": "host-local",
            "model": "say",
            "required_capabilities": ["wav"],
        },
        "transcriber": {
            "provider": "host-local",
            "model": "host-asr",
            "required_capabilities": ["timestamps"],
        },
        "fallbacks": [],
        "pricing_version": "local-v1",
        "max_request_cost": "0.00",
        "max_episode_cost": "0.00",
    }

    assert ProviderRoute.from_record(record).to_record() == record
    record["max_episode_cost"] = "0.00"
    record["max_request_cost"] = "0.01"
    with pytest.raises(ValueError, match="max_episode_cost"):
        ProviderRoute.from_record(record)
