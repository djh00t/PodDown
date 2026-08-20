"""Fail-closed, secret-safe runtime settings for provider routes."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from poddown.provider_routes import ProviderBinding, ProviderRoute

_LIVE_RENDERER_MODELS = {
    "elevenlabs": frozenset({"eleven-multilingual-v2"}),
}
_LIVE_TRANSCRIBER_MODELS = frozenset(
    {"gpt-4o-transcribe", "gpt-4o-mini-transcribe", "whisper-1"}
)
_DEFAULT_TIMEOUT_SECONDS = 30.0
_MAX_TIMEOUT_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class ProviderRuntimeSettings:
    """Validated provider controls containing references, never credentials."""

    route: ProviderRoute
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    endpoint: str | None = None
    live_enabled: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.route, ProviderRoute):
            raise ValueError("route must be a ProviderRoute")
        timeout = _validate_timeout(self.timeout_seconds)
        endpoint = _validate_endpoint(self.endpoint)
        object.__setattr__(self, "timeout_seconds", timeout)
        object.__setattr__(self, "endpoint", endpoint)
        if type(self.live_enabled) is not bool:
            raise ValueError("live_enabled must be a boolean")
        if self.route.mode == "live-provider":
            _validate_live_route(self.route)
            if not self.live_enabled:
                raise ValueError(
                    "live-provider routes require explicit live enablement"
                )
            if endpoint is None:
                raise ValueError("live-provider routes require an HTTPS endpoint")
        else:
            if self.live_enabled:
                raise ValueError("local provider routes must not enable live dispatch")
            if endpoint is not None:
                raise ValueError("local provider routes must not configure an endpoint")
            _validate_local_route(self.route)

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str]
    ) -> ProviderRuntimeSettings:
        """Parse non-secret provider controls from an injected environment mapping."""
        route_json = environment.get("PODDOWN_PROVIDER_ROUTE_JSON")
        if not isinstance(route_json, str) or not route_json.strip():
            raise ValueError("PODDOWN_PROVIDER_ROUTE_JSON is required")
        try:
            route_record = json.loads(route_json)
        except json.JSONDecodeError as error:
            raise ValueError(
                "PODDOWN_PROVIDER_ROUTE_JSON must be valid JSON"
            ) from error
        if not isinstance(route_record, dict):
            raise ValueError("PODDOWN_PROVIDER_ROUTE_JSON must be a JSON object")

        timeout_seconds = _environment_timeout(environment)
        live_enabled = _environment_live_enabled(environment)
        endpoint = environment.get("PODDOWN_PROVIDER_ENDPOINT")
        if endpoint == "":
            endpoint = None
        return cls(
            route=ProviderRoute.from_record(route_record),
            timeout_seconds=timeout_seconds,
            endpoint=endpoint,
            live_enabled=live_enabled,
        )

    def to_record(self) -> dict[str, object]:
        """Return safe route metadata suitable for diagnostics and evidence."""
        return {
            **self.route.to_record(),
            "endpoint": self.endpoint,
            "timeout_seconds": self.timeout_seconds,
            "live_enabled": self.live_enabled,
        }


def _environment_timeout(environment: Mapping[str, str]) -> float:
    value = environment.get("PODDOWN_PROVIDER_TIMEOUT_SECONDS", "30")
    if not isinstance(value, str):
        raise ValueError("PODDOWN_PROVIDER_TIMEOUT_SECONDS must be a number")
    try:
        return float(value)
    except ValueError as error:
        raise ValueError("PODDOWN_PROVIDER_TIMEOUT_SECONDS must be a number") from error


def _environment_live_enabled(environment: Mapping[str, str]) -> bool:
    value = environment.get("PODDOWN_PROVIDER_LIVE_ENABLED", "0")
    if value == "0":
        return False
    if value == "1":
        return True
    raise ValueError("PODDOWN_PROVIDER_LIVE_ENABLED must be 0 or 1")


def _validate_timeout(timeout_seconds: float) -> float:
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or not 0 < timeout_seconds <= _MAX_TIMEOUT_SECONDS
    ):
        raise ValueError("provider timeout must be finite and between 0 and 300")
    return float(timeout_seconds)


def _validate_endpoint(endpoint: str | None) -> str | None:
    if endpoint is None:
        return None
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise ValueError("provider endpoint must be an HTTPS URL")
    normalized = endpoint.strip()
    try:
        parsed = urlsplit(normalized)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ValueError("provider endpoint must be an HTTPS URL") from error
    if (
        parsed.scheme != "https"
        or hostname is None
        or port is None
        and parsed.netloc.endswith(":")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("provider endpoint must be an HTTPS URL")
    return normalized


def _validate_live_route(route: ProviderRoute) -> None:
    if route.renderer.provider != "elevenlabs":
        raise ValueError("live renderer must use ElevenLabs")
    if route.transcriber.provider != "openai":
        raise ValueError("live transcriber must use OpenAI")
    _validate_renderer(route.renderer)
    _validate_transcriber(route.transcriber)
    for fallback in route.fallbacks:
        _validate_live_fallback(fallback)
    if route.max_request_cost <= 0 or route.max_episode_cost <= 0:
        raise ValueError("live-provider routes require positive cost ceilings")


def _validate_renderer(binding: ProviderBinding) -> None:
    allowed_models = _LIVE_RENDERER_MODELS.get(binding.provider)
    if allowed_models is None or binding.model not in allowed_models:
        raise ValueError("live renderer provider and model are incompatible")
    if binding.voice_asset_id is None:
        raise ValueError("live renderer requires a voice asset reference")
    if not {"wav", "voice-pinning"} <= binding.required_capabilities:
        raise ValueError("live renderer capabilities are incomplete")


def _validate_transcriber(binding: ProviderBinding) -> None:
    if binding.provider != "openai" or binding.model not in _LIVE_TRANSCRIBER_MODELS:
        raise ValueError("live transcriber provider and model are incompatible")
    if binding.voice_asset_id is not None:
        raise ValueError("live transcriber must not have a voice asset reference")
    if "timestamps" not in binding.required_capabilities:
        raise ValueError("live transcriber capabilities are incomplete")


def _validate_live_fallback(binding: ProviderBinding) -> None:
    """Validate a fallback against its provider-specific role contract."""
    if binding.provider == "elevenlabs":
        _validate_renderer(binding)
    elif binding.provider == "openai":
        _validate_transcriber(binding)
    else:
        raise ValueError("live fallback provider is unsupported")


def _validate_local_route(route: ProviderRoute) -> None:
    if route.max_request_cost != 0 or route.max_episode_cost != 0:
        raise ValueError("local provider routes must have zero cost ceilings")


__all__ = ["ProviderRuntimeSettings"]
