"""Deterministic, provider-free production-readiness contracts."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class DependencyState(StrEnum):
    """Stable dependency states exposed by health and operations boundaries."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    """Immutable deterministic liveness/readiness projection."""

    liveness: str
    readiness: str
    dependencies: Mapping[str, DependencyState]

    def to_dict(self) -> dict[str, object]:
        return {
            "liveness": self.liveness,
            "readiness": self.readiness,
            "dependencies": {
                name: self.dependencies[name].value
                for name in sorted(self.dependencies)
            },
        }


class HealthEvaluator:
    """Evaluate health from injected state without probing external services."""

    def evaluate(
        self,
        *,
        liveness: bool,
        dependencies: Mapping[str, DependencyState] | None = None,
        probes: Mapping[str, Callable[[], bool]] | None = None,
    ) -> HealthSnapshot:
        states = {} if dependencies is None else dict(dependencies)
        if probes is not None:
            for name, probe in probes.items():
                try:
                    states[name] = (
                        DependencyState.HEALTHY
                        if probe()
                        else DependencyState.UNAVAILABLE
                    )
                except Exception:
                    states[name] = DependencyState.UNAVAILABLE
        normalized = {
            name: state
            if isinstance(state, DependencyState)
            else DependencyState(state)
            for name, state in states.items()
        }
        if not liveness:
            readiness = "unhealthy"
        elif any(state is not DependencyState.HEALTHY for state in normalized.values()):
            readiness = "degraded"
        else:
            readiness = "healthy"
        return HealthSnapshot(
            liveness="healthy" if liveness else "unhealthy",
            readiness=readiness,
            dependencies=MappingProxyType(dict(sorted(normalized.items()))),
        )


_REDACTED = "[REDACTED]"
_UNSAFE_KEY_PARTS = (
    "source",
    "script",
    "audio",
    "transcript",
    "voice",
    "credential",
    "password",
    "secret",
    "token",
    "api_key",
    "provider",
    "payload",
    "path",
    "uri",
)


def _is_unsafe_key(key: str) -> bool:
    normalized = "".join(
        character for character in key.casefold() if character.isalnum()
    )
    return any(
        "".join(character for character in part if character.isalnum()) in normalized
        for part in _UNSAFE_KEY_PARTS
    )


def _safe_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _safe_attributes({str(key): item for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_safe_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_safe_value(item) for item in value)
    if isinstance(value, (bytes, bytearray)):
        return _REDACTED
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError("operational event attributes must be JSON-safe")


def _safe_attributes(attributes: Mapping[str, object]) -> Mapping[str, object]:
    result: dict[str, object] = {}
    for key, value in attributes.items():
        if _is_unsafe_key(key):
            result[key] = _REDACTED
        else:
            result[key] = _safe_value(value)
    return MappingProxyType(dict(sorted(result.items())))


@dataclass(frozen=True, slots=True)
class OperationalEvent:
    """Tenant-safe structured event with recursively redacted attributes."""

    event_name: str
    tenant_id: str
    project_id: str
    correlation_id: str
    attributes: Mapping[str, object]

    @classmethod
    def create(
        cls,
        *,
        event_name: str,
        tenant_id: str,
        project_id: str,
        correlation_id: str,
        attributes: Mapping[str, object],
    ) -> OperationalEvent:
        if not all(
            isinstance(value, str) and value.strip()
            for value in (event_name, tenant_id, project_id, correlation_id)
        ):
            raise ValueError("operational event identity is required")
        return cls(
            event_name,
            tenant_id,
            project_id,
            correlation_id,
            _safe_attributes(attributes),
        )

    def to_dict(self) -> dict[str, object]:
        def json_safe(value: object) -> object:
            if isinstance(value, Mapping):
                return {str(key): json_safe(item) for key, item in value.items()}
            if isinstance(value, tuple):
                return [json_safe(item) for item in value]
            return value

        return {
            "event_name": self.event_name,
            "tenant_id": self.tenant_id,
            "project_id": self.project_id,
            "correlation_id": self.correlation_id,
            "attributes": json_safe(self.attributes),
        }


@dataclass(frozen=True, slots=True)
class MetricSample:
    """Tenant-safe numeric metric sample with bounded labels."""

    name: str
    value: float
    tenant_id: str
    project_id: str
    labels: Mapping[str, str]

    @classmethod
    def create(
        cls,
        *,
        name: str,
        value: float,
        tenant_id: str,
        project_id: str,
        labels: Mapping[str, str],
    ) -> MetricSample:
        if not all(
            isinstance(item, str) and item.strip()
            for item in (name, tenant_id, project_id)
        ):
            raise ValueError("metric identity is required")
        if any(_is_unsafe_key(key) for key in labels):
            raise ValueError("metric labels must be redacted")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError("metric value must be numeric")
        if not math.isfinite(float(value)):
            raise ValueError("metric value must be finite")
        if len(labels) > 12:
            raise ValueError("metric labels are too many")
        for key, label in labels.items():
            if not isinstance(key, str) or not key.strip() or len(key) > 64:
                raise ValueError("metric label keys must be bounded strings")
            if not isinstance(label, str) or not label.strip() or len(label) > 128:
                raise ValueError("metric label values must be bounded strings")
            if any(ord(character) < 32 for character in label):
                raise ValueError(
                    "metric label values must not contain control characters"
                )
            if _is_unsafe_key(label) or any(
                marker in label.casefold()
                for marker in ("http://", "https://", "{", "}")
            ):
                raise ValueError("metric label values must be safe summaries")
        return cls(
            name,
            float(value),
            tenant_id,
            project_id,
            MappingProxyType(dict(sorted(labels.items()))),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "value": self.value,
            "tenant_id": self.tenant_id,
            "project_id": self.project_id,
            "labels": dict(self.labels),
        }


__all__ = [
    "DependencyState",
    "HealthEvaluator",
    "HealthSnapshot",
    "MetricSample",
    "OperationalEvent",
]
