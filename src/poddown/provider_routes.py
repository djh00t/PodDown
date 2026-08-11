"""Immutable, secret-safe provider routing contracts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

_MODES = frozenset({"deterministic-local", "host-local", "live-provider"})
_PROVIDERS = frozenset({"local-system-tts-demo", "elevenlabs", "openai"})
_SECRET_REFERENCE_PREFIXES = ("env://", "keychain://", "secret://", "vault://")


def _non_empty_string(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _cost_limit(name: str, value: object) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be a finite non-negative Decimal")
    return value


@dataclass(frozen=True, slots=True)
class ProviderBinding:
    """One immutable provider binding with a secret reference, never a secret value."""

    provider: str
    model: str
    required_capabilities: frozenset[str]
    voice_asset_id: str | None = None
    secret_ref: str | None = None

    def __post_init__(self) -> None:
        if self.provider not in _PROVIDERS:
            raise ValueError("provider must be a supported provider")
        _non_empty_string("model", self.model)
        if self.voice_asset_id is not None:
            _non_empty_string("voice_asset_id", self.voice_asset_id)
        if not isinstance(self.required_capabilities, frozenset) or not all(
            isinstance(capability, str) and capability.strip()
            for capability in self.required_capabilities
        ):
            raise ValueError(
                "required_capabilities must be a frozenset of non-empty strings"
            )
        if self.secret_ref is not None and (
            not isinstance(self.secret_ref, str)
            or not self.secret_ref.startswith(_SECRET_REFERENCE_PREFIXES)
            or self.secret_ref in _SECRET_REFERENCE_PREFIXES
        ):
            raise ValueError("secret_ref must be a non-empty secret reference")
        if self.provider == "local-system-tts-demo" and self.secret_ref is not None:
            raise ValueError("local provider bindings must not have a secret_ref")
        if self.provider != "local-system-tts-demo" and self.secret_ref is None:
            raise ValueError("provider bindings require a secret_ref")


@dataclass(frozen=True, slots=True)
class ProviderRoute:
    """Immutable route policy for one renderer, transcriber, and fallback sequence."""

    route_id: str
    mode: str
    renderer: ProviderBinding
    transcriber: ProviderBinding
    fallbacks: tuple[ProviderBinding, ...]
    pricing_version: str
    max_request_cost: Decimal
    max_episode_cost: Decimal

    def __post_init__(self) -> None:
        _non_empty_string("route_id", self.route_id)
        if self.mode not in _MODES:
            raise ValueError("mode must be a supported execution mode")
        if not isinstance(self.renderer, ProviderBinding):
            raise ValueError("renderer must be a ProviderBinding")
        if not isinstance(self.transcriber, ProviderBinding):
            raise ValueError("transcriber must be a ProviderBinding")
        if not isinstance(self.fallbacks, tuple) or not all(
            isinstance(fallback, ProviderBinding) for fallback in self.fallbacks
        ):
            raise ValueError("fallbacks must be a tuple of ProviderBinding")
        _non_empty_string("pricing_version", self.pricing_version)
        _cost_limit("max_request_cost", self.max_request_cost)
        _cost_limit("max_episode_cost", self.max_episode_cost)
        if self.max_episode_cost < self.max_request_cost:
            raise ValueError("max_episode_cost must be at least max_request_cost")
        bindings = (self.renderer, self.transcriber, *self.fallbacks)
        if self.mode == "live-provider" and any(
            binding.provider == "local-system-tts-demo" for binding in bindings
        ):
            raise ValueError("live-provider routes cannot use local provider bindings")
        if self.mode != "live-provider" and any(
            binding.provider != "local-system-tts-demo" for binding in bindings
        ):
            raise ValueError("local routes require local provider bindings")


__all__ = ["ProviderBinding", "ProviderRoute"]
