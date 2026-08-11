"""Immutable, secret-safe provider routing contracts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

_MODES = frozenset({"deterministic-local", "host-local", "live-provider"})
_PROVIDERS = frozenset({"local", "host-local", "elevenlabs", "openai"})
_SECRET_REFERENCE_PREFIXES = ("env://", "keychain://", "secret://", "vault://")
_DECIMAL_PATTERN = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_MODE_PROVIDERS = {
    "deterministic-local": frozenset({"local"}),
    "host-local": frozenset({"host-local"}),
    "live-provider": frozenset({"elevenlabs", "openai"}),
}


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
        if self.provider in {"local", "host-local"} and self.secret_ref is not None:
            raise ValueError("local provider bindings must not have a secret_ref")
        if self.provider in {"elevenlabs", "openai"} and self.secret_ref is None:
            raise ValueError("provider bindings require a secret_ref")

    @classmethod
    def from_record(cls, record: Mapping[str, object]) -> ProviderBinding:
        """Normalize one schema-compatible JSON binding record."""
        allowed = {
            "provider",
            "model",
            "voice_asset_id",
            "required_capabilities",
            "secret_ref",
        }
        required = {"provider", "model", "required_capabilities"}
        if set(record) - allowed or not required <= set(record):
            raise ValueError("provider binding record fields are invalid")
        capabilities = record.get("required_capabilities")
        if (
            not isinstance(capabilities, list)
            or not all(isinstance(capability, str) for capability in capabilities)
            or len(set(capabilities)) != len(capabilities)
        ):
            raise ValueError("required_capabilities must be a JSON array")
        return cls(
            provider=record.get("provider"),  # type: ignore[arg-type]
            model=record.get("model"),  # type: ignore[arg-type]
            voice_asset_id=record.get("voice_asset_id"),  # type: ignore[arg-type]
            required_capabilities=frozenset(capabilities),
            secret_ref=record.get("secret_ref"),  # type: ignore[arg-type]
        )

    def to_record(self) -> dict[str, object]:
        """Return the JSON-safe binding representation without secret values."""
        record: dict[str, object] = {
            "provider": self.provider,
            "model": self.model,
            "required_capabilities": sorted(self.required_capabilities),
        }
        if self.voice_asset_id is not None:
            record["voice_asset_id"] = self.voice_asset_id
        if self.secret_ref is not None:
            record["secret_ref"] = self.secret_ref
        return record


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
        allowed_providers = _MODE_PROVIDERS[self.mode]
        bindings = (self.renderer, self.transcriber, *self.fallbacks)
        if any(binding.provider not in allowed_providers for binding in bindings):
            raise ValueError("provider binding is incompatible with route mode")

    @classmethod
    def from_record(cls, record: Mapping[str, object]) -> ProviderRoute:
        """Normalize one schema-compatible JSON route record, fail-closed."""
        required = {
            "route_id",
            "mode",
            "renderer",
            "transcriber",
            "fallbacks",
            "pricing_version",
            "max_request_cost",
            "max_episode_cost",
        }
        if set(record) != required:
            raise ValueError("provider route record fields are invalid")
        renderer = record["renderer"]
        transcriber = record["transcriber"]
        fallbacks = record["fallbacks"]
        if not isinstance(renderer, Mapping) or not isinstance(transcriber, Mapping):
            raise ValueError("provider route bindings must be JSON objects")
        if not isinstance(fallbacks, list) or not all(
            isinstance(fallback, Mapping) for fallback in fallbacks
        ):
            raise ValueError("fallbacks must be a JSON array of bindings")
        return cls(
            route_id=record["route_id"],  # type: ignore[arg-type]
            mode=record["mode"],  # type: ignore[arg-type]
            renderer=ProviderBinding.from_record(renderer),
            transcriber=ProviderBinding.from_record(transcriber),
            fallbacks=tuple(
                ProviderBinding.from_record(fallback) for fallback in fallbacks
            ),
            pricing_version=record["pricing_version"],  # type: ignore[arg-type]
            max_request_cost=_record_cost(
                "max_request_cost", record["max_request_cost"]
            ),
            max_episode_cost=_record_cost(
                "max_episode_cost", record["max_episode_cost"]
            ),
        )

    def to_record(self) -> dict[str, object]:
        """Return the schema-compatible JSON representation of this route."""
        return {
            "route_id": self.route_id,
            "mode": self.mode,
            "renderer": self.renderer.to_record(),
            "transcriber": self.transcriber.to_record(),
            "fallbacks": [fallback.to_record() for fallback in self.fallbacks],
            "pricing_version": self.pricing_version,
            "max_request_cost": format(self.max_request_cost, "f"),
            "max_episode_cost": format(self.max_episode_cost, "f"),
        }


def _record_cost(name: str, value: object) -> Decimal:
    if not isinstance(value, str) or _DECIMAL_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must be a Decimal string")
    try:
        return _cost_limit(name, Decimal(value))
    except Exception as error:
        raise ValueError(f"{name} must be a finite non-negative Decimal") from error


__all__ = ["ProviderBinding", "ProviderRoute"]
