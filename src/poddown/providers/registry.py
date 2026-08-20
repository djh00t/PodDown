"""Secret-safe registration and resolution for configured provider routes."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from poddown.provider_routes import ProviderBinding
from poddown.providers.settings import ProviderRuntimeSettings

_MODES = frozenset({"deterministic-local", "host-local", "live-provider"})
_MODE_PROVIDERS = {
    "deterministic-local": frozenset({"local"}),
    "host-local": frozenset({"host-local"}),
    "live-provider": frozenset({"elevenlabs", "openai"}),
}


def _required_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _text_set(name: str, value: object) -> frozenset[str]:
    if not isinstance(value, frozenset) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"{name} must be a frozenset of non-empty strings")
    return value


@dataclass(frozen=True, slots=True)
class ProviderRegistration:
    """One supported provider, mode, model, capability, and voice set."""

    provider: str
    mode: str
    model: str
    capabilities: frozenset[str]
    voice_asset_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        _required_text("provider", self.provider)
        if self.mode not in _MODES:
            raise ValueError("mode must be a supported execution mode")
        if self.provider not in _MODE_PROVIDERS[self.mode]:
            raise ValueError("provider is incompatible with the execution mode")
        _required_text("model", self.model)
        _text_set("capabilities", self.capabilities)
        _text_set("voice_asset_ids", self.voice_asset_ids)
        if self.voice_asset_ids and "voice-pinning" not in self.capabilities:
            raise ValueError("voice asset metadata requires voice-pinning capability")


@dataclass(frozen=True, slots=True)
class ResolvedProviderBinding:
    """Configured binding metadata without secret references or credential values."""

    provider: str
    model: str
    capabilities: frozenset[str]
    voice_asset_id: str | None
    voice_asset_ids: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ResolvedProviderRoute:
    """Registered route containing resolved provider capability metadata."""

    route_id: str
    mode: str
    renderer: ResolvedProviderBinding
    transcriber: ResolvedProviderBinding
    fallbacks: tuple[ResolvedProviderBinding, ...]
    pricing_version: str
    max_request_cost: Decimal
    max_episode_cost: Decimal

    def __post_init__(self) -> None:
        """Keep the safe route projection aligned with the route cost contract."""
        if (
            not isinstance(self.pricing_version, str)
            or not self.pricing_version.strip()
        ):
            raise ValueError("pricing_version must be a non-empty string")
        for name, value in (
            ("max_request_cost", self.max_request_cost),
            ("max_episode_cost", self.max_episode_cost),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be a finite non-negative Decimal")
        if self.max_episode_cost < self.max_request_cost:
            raise ValueError("max_episode_cost must be at least max_request_cost")


class ProviderRegistry:
    """Resolve registered provider capability metadata for validated settings."""

    def __init__(self) -> None:
        self._registrations: dict[tuple[str, str, str], ProviderRegistration] = {}
        self._routes: dict[str, ResolvedProviderRoute] = {}

    def register(self, registration: ProviderRegistration) -> None:
        """Register one exact provider, mode, and model declaration."""
        if not isinstance(registration, ProviderRegistration):
            raise ValueError("registration must be a ProviderRegistration")
        key = (registration.mode, registration.provider, registration.model)
        if key in self._registrations:
            raise ValueError("provider registration already exists")
        self._registrations[key] = registration

    def register_route(self, settings: ProviderRuntimeSettings) -> None:
        """Validate and retain one configured route as secret-free metadata."""
        if not isinstance(settings, ProviderRuntimeSettings):
            raise ValueError("settings must be ProviderRuntimeSettings")
        route = settings.route
        if route.route_id in self._routes:
            raise ValueError("provider route already exists")
        self._routes[route.route_id] = ResolvedProviderRoute(
            route_id=route.route_id,
            mode=route.mode,
            renderer=self._resolve_binding(route.mode, route.renderer),
            transcriber=self._resolve_binding(route.mode, route.transcriber),
            fallbacks=tuple(
                self._resolve_binding(route.mode, fallback)
                for fallback in route.fallbacks
            ),
            pricing_version=route.pricing_version,
            max_request_cost=route.max_request_cost,
            max_episode_cost=route.max_episode_cost,
        )

    def resolve(self, route_id: str) -> ResolvedProviderRoute:
        """Return one previously registered, capability-validated route."""
        _required_text("route_id", route_id)
        try:
            return self._routes[route_id]
        except KeyError as error:
            raise ValueError("provider route is not registered") from error

    def _resolve_binding(
        self, mode: str, binding: ProviderBinding
    ) -> ResolvedProviderBinding:
        registration = self._registrations.get((mode, binding.provider, binding.model))
        if registration is None:
            raise ValueError("provider binding is not registered")
        if not binding.required_capabilities <= registration.capabilities:
            raise ValueError("provider binding capabilities are not registered")
        if (
            binding.voice_asset_id is not None
            and binding.voice_asset_id not in registration.voice_asset_ids
        ):
            raise ValueError("provider binding voice asset is not registered")
        return ResolvedProviderBinding(
            provider=binding.provider,
            model=binding.model,
            capabilities=binding.required_capabilities,
            voice_asset_id=binding.voice_asset_id,
            voice_asset_ids=registration.voice_asset_ids,
        )


__all__ = [
    "ProviderRegistration",
    "ProviderRegistry",
    "ResolvedProviderBinding",
    "ResolvedProviderRoute",
]
