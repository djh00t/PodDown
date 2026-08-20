"""Fail-closed, secret-free provider dispatch preflight policy."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from poddown.audio.rights import VoiceConsent
from poddown.providers.registry import (
    ProviderRegistry,
    ResolvedProviderBinding,
    ResolvedProviderRoute,
)


class ProviderDispatchReason(StrEnum):
    """Stable policy reasons for a provider dispatch decision."""

    ROUTE_NOT_REGISTERED = "route_not_registered"
    METADATA_INCOMPLETE = "metadata_incomplete"
    BINDING_NOT_ELIGIBLE = "binding_not_eligible"
    CONSENT_MISSING = "consent_missing"
    CONSENT_INVALID = "consent_invalid"
    CONSENT_EVIDENCE_MISSING = "consent_evidence_missing"
    CONSENT_VOICE_MISMATCH = "consent_voice_mismatch"
    CONSENT_PROVIDER_NOT_ALLOWED = "consent_provider_not_allowed"
    REQUEST_BUDGET_EXCEEDED = "request_budget_exceeded"
    EPISODE_BUDGET_EXCEEDED = "episode_budget_exceeded"


@dataclass(frozen=True, slots=True)
class ProviderDispatchRequest:
    """Secret-free metadata needed to authorize one provider operation."""

    episode_id: str
    route_id: str
    operation: str
    provider: str
    model: str
    voice_asset_id: str | None
    required_capabilities: frozenset[str]
    estimated_cost: Decimal
    episode_cost: Decimal


@dataclass(frozen=True, slots=True)
class ProviderDispatchDecision:
    """Preflight outcome suitable for workflow evidence and QA assertions."""

    allowed: bool
    reasons: tuple[ProviderDispatchReason, ...]

    def __post_init__(self) -> None:
        if self.allowed != (not self.reasons):
            raise ValueError("allowed must match whether reasons are empty")


class ProviderDispatchPreflight:
    """Evaluate route, rights, capability, and budget policy before dispatch."""

    def __init__(self, registry: ProviderRegistry) -> None:
        if not isinstance(registry, ProviderRegistry):
            raise ValueError("registry must be a ProviderRegistry")
        self._registry = registry

    def evaluate(
        self, request: ProviderDispatchRequest, consent: VoiceConsent | None
    ) -> ProviderDispatchDecision:
        """Return every applicable fail-closed reason without provider interaction."""
        if not isinstance(request, ProviderDispatchRequest):
            raise ValueError("request must be a ProviderDispatchRequest")

        metadata_reasons = _metadata_reasons(request)
        reasons = list(metadata_reasons)
        route = self._resolve_route(request, reasons)
        if route is not None and not metadata_reasons:
            binding = _binding_for_operation(route, request.operation)
            if binding is None or not _matches_binding(binding, request):
                reasons.append(ProviderDispatchReason.BINDING_NOT_ELIGIBLE)
            if (
                request.operation == "render"
                and binding is not None
                and _matches_binding(binding, request)
            ):
                reasons.extend(
                    _consent_reasons(binding, request.voice_asset_id, consent)
                )
            reasons.extend(_budget_reasons(route, request))
        return ProviderDispatchDecision(allowed=not reasons, reasons=tuple(reasons))

    def _resolve_route(
        self,
        request: ProviderDispatchRequest,
        reasons: list[ProviderDispatchReason],
    ) -> ResolvedProviderRoute | None:
        try:
            return self._registry.resolve(request.route_id)
        except ValueError:
            reasons.append(ProviderDispatchReason.ROUTE_NOT_REGISTERED)
            return None


def _metadata_reasons(
    request: ProviderDispatchRequest,
) -> tuple[ProviderDispatchReason, ...]:
    fields = (
        request.episode_id,
        request.route_id,
        request.operation,
        request.provider,
        request.model,
    )
    if (
        not all(isinstance(value, str) and value.strip() for value in fields)
        or request.operation not in {"render", "transcribe"}
        or not (
            request.voice_asset_id is None
            or (
                isinstance(request.voice_asset_id, str)
                and request.voice_asset_id.strip()
            )
        )
        or not isinstance(request.required_capabilities, frozenset)
        or not all(
            isinstance(capability, str) and capability.strip()
            for capability in request.required_capabilities
        )
        or not _valid_cost(request.estimated_cost)
        or not _valid_cost(request.episode_cost)
    ):
        return (ProviderDispatchReason.METADATA_INCOMPLETE,)
    return ()


def _binding_for_operation(
    route: ResolvedProviderRoute, operation: str
) -> ResolvedProviderBinding | None:
    if operation == "render":
        return route.renderer
    if operation == "transcribe":
        return route.transcriber
    return None


def _matches_binding(
    binding: ResolvedProviderBinding, request: ProviderDispatchRequest
) -> bool:
    voice_matches = (
        request.voice_asset_id is None
        if binding.voice_asset_id is None
        else request.voice_asset_id
        in (binding.voice_asset_ids or frozenset({binding.voice_asset_id}))
    )
    return (
        binding.provider == request.provider
        and binding.model == request.model
        and voice_matches
        and binding.capabilities == request.required_capabilities
    )


def _consent_reasons(
    binding: ResolvedProviderBinding | None,
    voice_asset_id: str | None,
    consent: VoiceConsent | None,
) -> tuple[ProviderDispatchReason, ...]:
    if consent is None:
        return (ProviderDispatchReason.CONSENT_MISSING,)
    if consent.valid is not True:
        return (ProviderDispatchReason.CONSENT_INVALID,)
    if not consent.evidence_id:
        return (ProviderDispatchReason.CONSENT_EVIDENCE_MISSING,)
    if binding is None or binding.voice_asset_id is None or voice_asset_id is None:
        return (ProviderDispatchReason.CONSENT_VOICE_MISMATCH,)
    if consent.voice_asset_id != voice_asset_id:
        return (ProviderDispatchReason.CONSENT_VOICE_MISMATCH,)
    if binding.provider not in consent.allowed_providers:
        return (ProviderDispatchReason.CONSENT_PROVIDER_NOT_ALLOWED,)
    return ()


def _budget_reasons(
    route: ResolvedProviderRoute, request: ProviderDispatchRequest
) -> tuple[ProviderDispatchReason, ...]:
    if not _valid_cost(request.estimated_cost) or not _valid_cost(request.episode_cost):
        return ()
    reasons: list[ProviderDispatchReason] = []
    if request.estimated_cost > route.max_request_cost:
        reasons.append(ProviderDispatchReason.REQUEST_BUDGET_EXCEEDED)
    if request.episode_cost + request.estimated_cost > route.max_episode_cost:
        reasons.append(ProviderDispatchReason.EPISODE_BUDGET_EXCEEDED)
    return tuple(reasons)


def _valid_cost(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value >= 0


__all__ = [
    "ProviderDispatchDecision",
    "ProviderDispatchPreflight",
    "ProviderDispatchReason",
    "ProviderDispatchRequest",
]
