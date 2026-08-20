"""Acceptance tests for fail-closed provider dispatch preflight."""

from decimal import Decimal

from pytest_bdd import given, scenarios, then, when

from poddown.audio.rights import VoiceConsent
from poddown.provider_routes import ProviderBinding, ProviderRoute
from poddown.providers.policy import (
    ProviderDispatchPreflight,
    ProviderDispatchReason,
    ProviderDispatchRequest,
)
from poddown.providers.registry import ProviderRegistration, ProviderRegistry
from poddown.providers.settings import ProviderRuntimeSettings
from tests.bdd.conftest import ScenarioContext

scenarios("../features/provider_dispatch_policy.feature")


def _live_registry() -> ProviderRegistry:
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
    registry.register_route(
        ProviderRuntimeSettings(
            route=ProviderRoute(
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
            ),
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


def _consent(*, valid: bool = True) -> VoiceConsent:
    return VoiceConsent(
        voice_asset_id="voice-public-1",
        evidence_id="consent-1",
        allowed_providers=frozenset({"elevenlabs"}),
        valid=valid,
    )


@given("a complete registered live render request")
def complete_live_render_request(context: ScenarioContext) -> None:
    """Provide exact live route metadata and budget headroom."""
    context.values["preflight"] = ProviderDispatchPreflight(_live_registry())
    context.values["request"] = _request()
    context.values["consent"] = _consent()


@given("valid consent for the configured voice and provider")
def valid_consent(context: ScenarioContext) -> None:
    """Provide current provider-scoped voice consent."""
    context.values["consent"] = _consent()


@given("revoked consent for the configured voice and provider")
def revoked_consent(context: ScenarioContext) -> None:
    """Provide revoked consent for the configured voice."""
    context.values["consent"] = _consent(valid=False)


@given("a request that exceeds both cost budgets")
def request_exceeds_budgets(context: ScenarioContext) -> None:
    """Set request and cumulative episode estimates above their ceilings."""
    context.values["request"] = _request(
        estimated_cost=Decimal("0.26"), episode_cost=Decimal("4.90")
    )


@given("a complete registered live transcription request")
def complete_live_transcription_request(context: ScenarioContext) -> None:
    """Provide the exact registered transcriber binding."""
    context.values["preflight"] = ProviderDispatchPreflight(_live_registry())
    context.values["request"] = _request(
        operation="transcribe",
        provider="openai",
        model="whisper-1",
        voice_asset_id=None,
        required_capabilities=frozenset({"timestamps"}),
    )
    context.values["consent"] = None


@given("the request names an unknown route")
def unknown_route(context: ScenarioContext) -> None:
    """Change only the route identity after the valid request is prepared."""
    context.values["request"] = _request(route_id="missing-route")


@given("a complete deterministic-local render request")
def complete_local_render_request(context: ScenarioContext) -> None:
    """Prepare a zero-cost local route with no secret reference."""
    registry = ProviderRegistry()
    binding = ProviderBinding(
        provider="local",
        model="local-renderer-v1",
        required_capabilities=frozenset({"voice-pinning", "wav"}),
        voice_asset_id="local-voice-1",
    )
    registry.register(
        ProviderRegistration(
            provider="local",
            mode="deterministic-local",
            model="local-renderer-v1",
            capabilities=frozenset({"voice-pinning", "wav"}),
            voice_asset_ids=frozenset({"local-voice-1"}),
        )
    )
    registry.register(
        ProviderRegistration(
            provider="local",
            mode="deterministic-local",
            model="local-transcriber-v1",
            capabilities=frozenset({"timestamps"}),
        )
    )
    registry.register_route(
        ProviderRuntimeSettings(
            route=ProviderRoute(
                route_id="local-route",
                mode="deterministic-local",
                renderer=binding,
                transcriber=ProviderBinding(
                    provider="local",
                    model="local-transcriber-v1",
                    required_capabilities=frozenset({"timestamps"}),
                ),
                fallbacks=(),
                pricing_version="local-v1",
                max_request_cost=Decimal("0"),
                max_episode_cost=Decimal("0"),
            )
        )
    )
    context.values["preflight"] = ProviderDispatchPreflight(registry)
    context.values["request"] = ProviderDispatchRequest(
        episode_id="episode-local",
        route_id="local-route",
        operation="render",
        provider="local",
        model="local-renderer-v1",
        voice_asset_id="local-voice-1",
        required_capabilities=frozenset({"voice-pinning", "wav"}),
        estimated_cost=Decimal("0"),
        episode_cost=Decimal("0"),
    )
    context.values["consent"] = VoiceConsent(
        voice_asset_id="local-voice-1",
        evidence_id="local-consent",
        allowed_providers=frozenset({"local"}),
    )
    context.values["resolved"] = registry.resolve("local-route")


@when("provider dispatch preflight is evaluated")
def evaluate_preflight(context: ScenarioContext) -> None:
    """Evaluate policy without invoking a provider adapter."""
    context.values["decision"] = context.values["preflight"].evaluate(
        context.values["request"], context.values["consent"]
    )


@then("dispatch is allowed without policy reasons")
def dispatch_allowed(context: ScenarioContext) -> None:
    """Accept only a complete policy-compliant request."""
    decision = context.values["decision"]
    assert decision.allowed is True
    assert decision.reasons == ()


@then("dispatch is denied with consent and budget reasons")
def dispatch_denied_with_consent_and_budget(context: ScenarioContext) -> None:
    """Expose stable reasons for rights and both budget failures."""
    decision = context.values["decision"]
    assert decision.reasons == (
        ProviderDispatchReason.CONSENT_INVALID,
        ProviderDispatchReason.REQUEST_BUDGET_EXCEEDED,
        ProviderDispatchReason.EPISODE_BUDGET_EXCEEDED,
    )


@then("dispatch is denied because the route is not registered")
def dispatch_denied_unknown_route(context: ScenarioContext) -> None:
    """Unknown route identity is terminal before provider dispatch."""
    decision = context.values["decision"]
    assert decision.allowed is False
    assert decision.reasons == (ProviderDispatchReason.ROUTE_NOT_REGISTERED,)


@then("the resolved local route has zero cost and no secret metadata")
def local_route_is_credential_free(context: ScenarioContext) -> None:
    """Local resolution exposes no paid ceiling or secret reference."""
    resolved = context.values["resolved"]
    assert resolved.max_request_cost == Decimal("0")
    assert resolved.max_episode_cost == Decimal("0")
    assert "secret" not in repr(resolved).lower()
