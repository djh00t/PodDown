"""Explicit live-provider runtime construction with no local fallback."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from poddown.audio.elevenlabs import (
    ElevenLabsAudioRenderer,
    RoutedElevenLabsAudioRenderer,
)
from poddown.audio.rights import VoiceConsent
from poddown.content.adaptation_envelope import AdaptationUsage
from poddown.content.live_adaptation import (
    LiveAdaptationService,
    ReasoningEvidenceRecorder,
)
from poddown.content.openai_reasoning import OpenAIResponsesTransport
from poddown.provider_routes import ProviderBinding
from poddown.providers.contracts import Transcriber
from poddown.providers.http import ProviderSettings
from poddown.providers.http_transport import UrllibAsyncHttpTransport
from poddown.providers.policy import ProviderDispatchPreflight
from poddown.providers.provider_factory import (
    OpenAITranscriberFactory,
    OpenAITranscriptionPricing,
)
from poddown.providers.registry import ProviderRegistration, ProviderRegistry
from poddown.providers.settings import ProviderRuntimeSettings
from poddown.workflow_snapshots import WorkflowRenderBinding


def _required_environment(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _secret_environment_name(binding: ProviderBinding) -> str:
    reference = binding.secret_ref
    if not isinstance(reference, str) or not reference.startswith("env://"):
        raise ValueError("live provider secrets must use env:// references")
    name = reference.removeprefix("env://")
    if not name or "/" in name or "\\" in name or name.strip() != name:
        raise ValueError("live provider secret reference is invalid")
    return name


def _mapping_environment(environment: Mapping[str, str], name: str) -> dict[str, str]:
    raw = _required_environment(environment, name)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"{name} must be a JSON object") from error
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{name} must be a non-empty JSON object")
    if any(
        not isinstance(key, str)
        or not key.strip()
        or not isinstance(item, str)
        or not item.strip()
        for key, item in value.items()
    ):
        raise ValueError(f"{name} must map non-empty strings to non-empty strings")
    return {str(key): str(item) for key, item in value.items()}


def _positive_decimal(
    environment: Mapping[str, str], name: str, *, default: Decimal | None = None
) -> Decimal:
    raw = environment.get(name)
    if raw is None and default is not None:
        return default
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{name} is required")
    try:
        value = Decimal(raw)
    except InvalidOperation as error:
        raise ValueError(f"{name} must be a positive Decimal") from error
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{name} must be a positive Decimal")
    return value


def _boolean_environment(environment: Mapping[str, str], name: str) -> bool:
    value = environment.get(name, "0")
    if value not in {"0", "1"}:
        raise ValueError(f"{name} must be 0 or 1")
    return value == "1"


@dataclass(frozen=True, slots=True)
class LiveProviderRuntime:
    """All provider ports required for one explicitly enabled live route."""

    settings: ProviderRuntimeSettings
    registry: ProviderRegistry
    preflight: ProviderDispatchPreflight
    renderer: RoutedElevenLabsAudioRenderer
    transcriber: Transcriber
    reasoning: LiveAdaptationService
    render_binding: WorkflowRenderBinding
    render_estimated_cost: Decimal
    transcription_estimated_cost: Decimal

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str],
        *,
        reasoning_evidence_recorder: ReasoningEvidenceRecorder | None = None,
    ) -> LiveProviderRuntime:
        """Construct live adapters without contacting a provider."""
        settings = ProviderRuntimeSettings.from_environment(environment)
        if settings.route.mode != "live-provider" or not settings.live_enabled:
            raise ValueError("live provider runtime requires explicit enablement")
        if settings.route.fallbacks:
            raise ValueError("live provider fallbacks are not configured")
        route = settings.route
        renderer_binding = route.renderer
        transcriber_binding = route.transcriber
        renderer_secret = _secret_environment_name(renderer_binding)
        transcriber_secret = _secret_environment_name(transcriber_binding)
        voice_map = _mapping_environment(environment, "PODDOWN_LIVE_VOICE_MAP_JSON")
        consent_map = _mapping_environment(environment, "PODDOWN_LIVE_CONSENT_JSON")
        provider_voice_ids = set(voice_map.values())
        if renderer_binding.voice_asset_id is not None:
            provider_voice_ids.add(renderer_binding.voice_asset_id)
        if any(voice_id not in consent_map for voice_id in voice_map.values()):
            raise ValueError("live provider voice consent evidence is incomplete")
        consents = {
            voice_id: VoiceConsent(
                voice_asset_id=voice_id,
                evidence_id=consent_map[voice_id],
                allowed_providers=frozenset({"elevenlabs"}),
            )
            for voice_id in voice_map.values()
        }

        registry = ProviderRegistry()
        registry.register(
            ProviderRegistration(
                provider=renderer_binding.provider,
                mode="live-provider",
                model=renderer_binding.model,
                capabilities=renderer_binding.required_capabilities,
                voice_asset_ids=frozenset(provider_voice_ids),
            )
        )
        registry.register(
            ProviderRegistration(
                provider=transcriber_binding.provider,
                mode="live-provider",
                model=transcriber_binding.model,
                capabilities=transcriber_binding.required_capabilities,
            )
        )
        registry.register_route(settings)
        preflight = ProviderDispatchPreflight(registry)
        transport = UrllibAsyncHttpTransport()
        elevenlabs_settings = ProviderSettings.from_environment(
            renderer_secret, environment, settings.timeout_seconds
        )
        cost_per_character = _positive_decimal(
            environment,
            "PODDOWN_ELEVENLABS_COST_PER_CHARACTER",
        )
        zero_retention = _boolean_environment(
            environment, "PODDOWN_ELEVENLABS_ZERO_RETENTION"
        )
        renderers = {
            voice_id: ElevenLabsAudioRenderer(
                __import__(
                    "poddown.providers.elevenlabs_client",
                    fromlist=["ElevenLabsClient"],
                ).ElevenLabsClient(
                    settings=elevenlabs_settings,
                    voice_id=voice_id,
                    model=renderer_binding.model,
                    transport=transport,
                    cost_per_character=cost_per_character,
                    max_cost_per_request=route.max_request_cost,
                    zero_retention=zero_retention,
                )
            )
            for voice_id in provider_voice_ids
        }
        transcriber_settings = ProviderSettings.from_environment(
            transcriber_secret, environment, settings.timeout_seconds
        )
        transcription_rate = _positive_decimal(
            environment,
            "PODDOWN_OPENAI_COST_PER_AUDIO_SECOND",
        )
        transcriber = OpenAITranscriberFactory(
            settings=transcriber_settings,
            transport=transport,
            pricing=OpenAITranscriptionPricing(
                {transcriber_binding.model: transcription_rate}
            ),
        ).create(transcriber_binding)
        reasoning_model = _required_environment(
            environment, "PODDOWN_OPENAI_REASONING_MODEL"
        )
        reasoning_input_rate = _positive_decimal(
            environment, "PODDOWN_OPENAI_COST_PER_REASONING_INPUT_TOKEN"
        )
        reasoning_output_rate = _positive_decimal(
            environment, "PODDOWN_OPENAI_COST_PER_REASONING_OUTPUT_TOKEN"
        )
        reasoning_transport = OpenAIResponsesTransport(
            model=reasoning_model,
            settings=transcriber_settings,
            transport=transport,
            cost_estimator=lambda usage: _reasoning_cost(
                usage, reasoning_input_rate, reasoning_output_rate
            ),
        )
        reasoning = LiveAdaptationService(
            reasoning_transport,
            evidence_recorder=reasoning_evidence_recorder,
            max_cost=route.max_request_cost,
        )
        max_attempts = int(environment.get("PODDOWN_WORKFLOW_MAX_ATTEMPTS", "2"))
        render_binding = WorkflowRenderBinding(
            provider=renderer_binding.provider,
            model=renderer_binding.model,
            consents=consents,
            max_attempts=max_attempts,
            voice_asset_ids=voice_map,
        )
        return cls(
            settings=settings,
            registry=registry,
            preflight=preflight,
            renderer=RoutedElevenLabsAudioRenderer(renderers),
            transcriber=transcriber,
            reasoning=reasoning,
            render_binding=render_binding,
            render_estimated_cost=_positive_decimal(
                environment,
                "PODDOWN_LIVE_RENDER_ESTIMATED_COST",
                default=route.max_request_cost,
            ),
            transcription_estimated_cost=_positive_decimal(
                environment,
                "PODDOWN_LIVE_TRANSCRIPTION_ESTIMATED_COST",
                default=route.max_request_cost,
            ),
        )


def _reasoning_cost(
    usage: AdaptationUsage, input_rate: Decimal, output_rate: Decimal
) -> Decimal:
    """Estimate structured-reasoning cost from normalized token usage."""
    if not isinstance(usage, AdaptationUsage):
        raise ValueError("reasoning usage must be normalized")
    values = usage.values
    return input_rate * Decimal(values["input_tokens"]) + output_rate * Decimal(
        values["output_tokens"]
    )


__all__ = ["LiveProviderRuntime"]
