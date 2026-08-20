"""Unit contracts for secret-safe provider capability resolution."""

from decimal import Decimal

import pytest

from poddown.provider_routes import ProviderBinding, ProviderRoute
from poddown.providers.registry import ProviderRegistration, ProviderRegistry
from poddown.providers.settings import ProviderRuntimeSettings


def _live_route(*, renderer_voice: str = "voice-public-1") -> ProviderRoute:
    return ProviderRoute(
        route_id="live-primary",
        mode="live-provider",
        renderer=ProviderBinding(
            provider="elevenlabs",
            model="eleven-multilingual-v2",
            required_capabilities=frozenset({"voice-pinning", "wav"}),
            voice_asset_id=renderer_voice,
            secret_ref="env://ELEVENLABS_API_KEY",
        ),
        transcriber=ProviderBinding(
            provider="openai",
            model="whisper-1",
            required_capabilities=frozenset({"timestamps"}),
            secret_ref="env://OPENAI_API_KEY",
        ),
        fallbacks=(),
        pricing_version="2026-08-14",
        max_request_cost=Decimal("0.25"),
        max_episode_cost=Decimal("5.00"),
    )


def _live_settings(route: ProviderRoute | None = None) -> ProviderRuntimeSettings:
    return ProviderRuntimeSettings(
        route=route or _live_route(),
        endpoint="https://providers.example.test/v1",
        live_enabled=True,
    )


def _registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(
        ProviderRegistration(
            provider="elevenlabs",
            mode="live-provider",
            model="eleven-multilingual-v2",
            capabilities=frozenset({"voice-pinning", "wav"}),
            voice_asset_ids=frozenset({"voice-public-1"}),
        )
    )
    registry.register(
        ProviderRegistration(
            provider="openai",
            mode="live-provider",
            model="whisper-1",
            capabilities=frozenset({"timestamps"}),
        )
    )
    return registry


def test_registry_resolves_registered_route_without_secrets() -> None:
    """Registered route bindings resolve in order without secret metadata."""
    registry = _registry()
    registry.register_route(_live_settings())

    resolved = registry.resolve("live-primary")

    assert resolved.renderer.provider == "elevenlabs"
    assert resolved.renderer.voice_asset_id == "voice-public-1"
    assert resolved.transcriber.model == "whisper-1"
    assert "env://" not in repr(resolved)


def test_registry_rejects_unregistered_model_capability_or_voice() -> None:
    """A route cannot select metadata outside the registry declaration."""
    registry = _registry()
    invalid_voice = _live_settings(_live_route(renderer_voice="voice-other"))

    with pytest.raises(ValueError, match="registered"):
        registry.register_route(invalid_voice)


def test_registry_rejects_missing_provider_registration() -> None:
    """Every route binding must have an explicit provider registration."""
    registry = ProviderRegistry()

    with pytest.raises(ValueError, match="registered"):
        registry.register_route(_live_settings())


def test_registry_rejects_duplicate_registrations_and_routes() -> None:
    """Registration is explicit and cannot silently replace existing policy."""
    registry = _registry()
    with pytest.raises(ValueError, match="already exists"):
        registry.register(
            ProviderRegistration(
                provider="openai",
                mode="live-provider",
                model="whisper-1",
                capabilities=frozenset({"timestamps"}),
            )
        )

    registry.register_route(_live_settings())
    with pytest.raises(ValueError, match="already exists"):
        registry.register_route(_live_settings())


def test_registry_resolves_deterministic_local_route_without_credentials() -> None:
    """Deterministic local resolution preserves offline provider identity."""
    binding = ProviderBinding(
        provider="local",
        model="local-deterministic-v1",
        required_capabilities=frozenset({"timestamps", "wav"}),
    )
    route = ProviderRoute(
        route_id="local-route",
        mode="deterministic-local",
        renderer=binding,
        transcriber=binding,
        fallbacks=(),
        pricing_version="local-v1",
        max_request_cost=Decimal("0"),
        max_episode_cost=Decimal("0"),
    )
    registry = ProviderRegistry()
    registry.register(
        ProviderRegistration(
            provider="local",
            mode="deterministic-local",
            model="local-deterministic-v1",
            capabilities=frozenset({"timestamps", "wav"}),
        )
    )

    registry.register_route(
        ProviderRuntimeSettings(route=route, live_enabled=False, endpoint=None)
    )

    assert registry.resolve("local-route").renderer.provider == "local"


@pytest.mark.parametrize(
    ("provider", "mode"),
    [("local", "live-provider"), ("host-local", "deterministic-local")],
)
def test_registration_rejects_mode_provider_mismatch(provider: str, mode: str) -> None:
    """Registration metadata must retain the same mode/provider matrix as routes."""
    with pytest.raises(ValueError, match="incompatible"):
        ProviderRegistration(
            provider=provider,
            mode=mode,
            model="model-v1",
            capabilities=frozenset({"wav"}),
        )
