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
