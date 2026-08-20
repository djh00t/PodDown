"""Unit contracts for fail-closed provider dispatch preflight."""

from decimal import Decimal

import pytest

from poddown.audio.rights import VoiceConsent
from poddown.provider_routes import ProviderBinding, ProviderRoute
from poddown.providers.policy import (
    ProviderDispatchDecision,
    ProviderDispatchPreflight,
    ProviderDispatchReason,
    ProviderDispatchRequest,
)
from poddown.providers.registry import ProviderRegistration, ProviderRegistry
from poddown.providers.settings import ProviderRuntimeSettings


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
    route = ProviderRoute(
        route_id="live-primary",
        mode="live-provider",
        renderer=ProviderBinding(
            provider="elevenlabs",
            model="eleven-multilingual-v2",
            required_capabilities=frozenset({"voice-pinning", "wav"}),
            voice_asset_id="voice-public-1",
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
    registry.register_route(
        ProviderRuntimeSettings(
            route=route,
            endpoint="https://providers.example.test/v1",
            live_enabled=True,
        )
    )
    return registry


def _request(**overrides: object) -> ProviderDispatchRequest:
    values: dict[str, object] = {
        "episode_id": "episode-1",
        "route_id": "live-primary",
        "operation": "render",
        "provider": "elevenlabs",
        "model": "eleven-multilingual-v2",
        "voice_asset_id": "voice-public-1",
        "required_capabilities": frozenset({"voice-pinning", "wav"}),
        "estimated_cost": Decimal("0.20"),
        "episode_cost": Decimal("4.70"),
    }
    values.update(overrides)
    return ProviderDispatchRequest(**values)  # type: ignore[arg-type]


def _consent(
    *,
    valid: bool = True,
    evidence_id: str = "consent-1",
    voice_asset_id: str = "voice-public-1",
    allowed_providers: frozenset[str] = frozenset({"elevenlabs"}),
) -> VoiceConsent:
    return VoiceConsent(
        voice_asset_id=voice_asset_id,
        evidence_id=evidence_id,
        allowed_providers=allowed_providers,
        valid=valid,
    )


def test_preflight_allows_complete_live_render_with_budget_headroom() -> None:
    """A complete registered live render is allowed before provider dispatch."""
    decision = ProviderDispatchPreflight(_registry()).evaluate(_request(), _consent())

    assert decision == ProviderDispatchDecision(allowed=True, reasons=())
    assert "API_KEY" not in repr(decision)


def test_preflight_returns_ordered_consent_and_budget_reasons() -> None:
    """Rights and both budget boundaries remain independently visible."""
    decision = ProviderDispatchPreflight(_registry()).evaluate(
        _request(estimated_cost=Decimal("0.26"), episode_cost=Decimal("4.90")),
        _consent(valid=False),
    )

    assert decision.reasons == (
        ProviderDispatchReason.CONSENT_INVALID,
        ProviderDispatchReason.REQUEST_BUDGET_EXCEEDED,
        ProviderDispatchReason.EPISODE_BUDGET_EXCEEDED,
    )


def test_preflight_rejects_provider_model_voice_or_capability_substitution() -> None:
    """A request must exactly match the registered renderer binding."""
    decision = ProviderDispatchPreflight(_registry()).evaluate(
        _request(
            provider="openai",
            model="whisper-1",
            voice_asset_id="voice-other-1",
            required_capabilities=frozenset({"timestamps"}),
        ),
        _consent(),
    )

    assert decision.reasons == (ProviderDispatchReason.BINDING_NOT_ELIGIBLE,)


def test_preflight_allows_registered_transcription_without_voice_consent() -> None:
    """Transcription uses the registered provider binding, not voice rights."""
    decision = ProviderDispatchPreflight(_registry()).evaluate(
        _request(
            operation="transcribe",
            provider="openai",
            model="whisper-1",
            voice_asset_id=None,
            required_capabilities=frozenset({"timestamps"}),
        ),
        None,
    )

    assert decision.allowed is True


def test_preflight_reports_unknown_route_and_malformed_metadata() -> None:
    """Missing route identity is reported alongside incomplete metadata."""
    missing_route = ProviderDispatchPreflight(_registry()).evaluate(
        _request(route_id="missing-route"), _consent()
    )
    malformed = ProviderDispatchPreflight(_registry()).evaluate(
        _request(estimated_cost=Decimal("NaN")), _consent()
    )

    assert missing_route.reasons == (ProviderDispatchReason.ROUTE_NOT_REGISTERED,)
    assert malformed.reasons == (ProviderDispatchReason.METADATA_INCOMPLETE,)


def test_preflight_rejects_missing_consent_evidence_and_provider_allowlist() -> None:
    """Consent must be current, evidenced, voice-matched, and provider-scoped."""
    preflight = ProviderDispatchPreflight(_registry())

    assert preflight.evaluate(_request(), None).reasons == (
        ProviderDispatchReason.CONSENT_MISSING,
    )
    assert preflight.evaluate(_request(), _consent(evidence_id="")).reasons == (
        ProviderDispatchReason.CONSENT_EVIDENCE_MISSING,
    )
    assert preflight.evaluate(
        _request(), _consent(allowed_providers=frozenset({"openai"}))
    ).reasons == (ProviderDispatchReason.CONSENT_PROVIDER_NOT_ALLOWED,)


def test_preflight_rejects_blank_voice_asset_metadata() -> None:
    """Blank voice identity cannot be used to bypass the consent boundary."""
    decision = ProviderDispatchPreflight(_registry()).evaluate(
        _request(voice_asset_id="  "), _consent()
    )

    assert decision.reasons == (ProviderDispatchReason.METADATA_INCOMPLETE,)


@pytest.mark.parametrize(
    "estimated_cost",
    [Decimal("0.25"), Decimal("0.26")],
)
def test_preflight_applies_request_budget_boundary(estimated_cost: Decimal) -> None:
    """The request ceiling is inclusive and local callers cannot incur cost."""
    decision = ProviderDispatchPreflight(_registry()).evaluate(
        _request(estimated_cost=estimated_cost, episode_cost=Decimal("0")),
        _consent(),
    )

    if estimated_cost == Decimal("0.25"):
        assert decision.allowed is True
    else:
        assert decision.reasons == (ProviderDispatchReason.REQUEST_BUDGET_EXCEEDED,)
