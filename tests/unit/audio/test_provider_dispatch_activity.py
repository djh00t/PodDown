"""Unit contracts for provider preflight at the durable activity boundary."""

import asyncio
from decimal import Decimal

import pytest
from temporalio.exceptions import ApplicationError

from poddown.audio import (
    DeterministicLocalRenderer,
    DurableRenderService,
    EpisodeWorkflowInput,
    ProviderActivityDispatchPolicy,
    RenderRequest,
    SegmentWorkflowInput,
    VoiceConsent,
    build_durable_render_activity,
    deterministic_quality_evaluator,
)
from poddown.audio.activities import ProviderDispatchRejectedError
from poddown.audio.storage import (
    FilesystemArtifactStore,
    FilesystemRenderRecordStore,
)
from poddown.provider_routes import ProviderBinding, ProviderRoute
from poddown.providers.policy import ProviderDispatchPreflight
from poddown.providers.registry import ProviderRegistration, ProviderRegistry
from poddown.providers.settings import ProviderRuntimeSettings


def _live_route() -> ProviderRoute:
    return ProviderRoute(
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


def _preflight(route: ProviderRoute) -> ProviderDispatchPreflight:
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
            route=route,
            endpoint="https://providers.example.test/v1",
            live_enabled=True,
        )
    )
    return ProviderDispatchPreflight(registry)


def _episode(consent: VoiceConsent) -> EpisodeWorkflowInput:
    request = RenderRequest(
        episode_id="episode-1",
        episode_version="v1",
        segment_id="segment-1",
        speaker_id="host",
        expected_spoken_text="Live policy must run before dispatch.",
        voice_asset_id="voice-public-1",
        provider="elevenlabs",
        model="eleven-multilingual-v2",
    )
    return EpisodeWorkflowInput(
        episode_id=request.episode_id,
        episode_version=request.episode_version,
        segments=(
            SegmentWorkflowInput(
                segment_id=request.segment_id,
                render_request=request,
                consent=consent,
                critical_tokens=("policy", "before"),
            ),
        ),
    )


def _payload(episode: EpisodeWorkflowInput) -> dict[str, object]:
    from poddown.audio.workflow import activity_key_for

    segment = episode.segments[0]
    return {
        "episode": episode.to_dict(),
        "segment": segment.to_dict(),
        "attempt": 1,
        "take": 0,
        "activity_key": activity_key_for(
            episode, "render", segment.segment_id, attempt=1, take=0
        ),
    }


def _activity(tmp_path, policy: ProviderActivityDispatchPolicy, renderer):
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    records = FilesystemRenderRecordStore(tmp_path / "records", artifacts)
    return build_durable_render_activity(
        DurableRenderService(artifacts, records),
        renderer,
        artifacts,
        quality_evaluator=deterministic_quality_evaluator,
        provider_dispatch=policy,
    )


def test_activity_denies_provider_dispatch_before_renderer_call(tmp_path) -> None:
    """Consent failure must be mapped before a provider-shaped renderer runs."""
    route = _live_route()
    policy = ProviderActivityDispatchPolicy(
        preflight=_preflight(route),
        route=route,
        render_estimated_cost=Decimal("0.20"),
        transcription_estimated_cost=Decimal("0.01"),
    )
    renderer = DeterministicLocalRenderer()
    activity = _activity(tmp_path, policy, renderer)
    episode = _episode(
        VoiceConsent(
            voice_asset_id="voice-public-1",
            evidence_id="consent-1",
            allowed_providers=frozenset({"openai"}),
        )
    )

    with pytest.raises(ApplicationError) as error:
        asyncio.run(activity(_payload(episode)))

    assert error.value.type == ProviderDispatchRejectedError.__name__
    assert error.value.non_retryable is True
    assert "consent_provider_not_allowed" in str(error.value)
    assert renderer.calls == []


def test_activity_runs_registered_preflight_before_durable_render(tmp_path) -> None:
    """A complete policy decision permits the subsequent durable render path."""
    route = _live_route()
    policy = ProviderActivityDispatchPolicy(
        preflight=_preflight(route),
        route=route,
        render_estimated_cost=Decimal("0.20"),
        transcription_estimated_cost=Decimal("0.01"),
    )
    renderer = DeterministicLocalRenderer()
    activity = _activity(tmp_path, policy, renderer)
    episode = _episode(
        VoiceConsent(
            voice_asset_id="voice-public-1",
            evidence_id="consent-1",
            allowed_providers=frozenset({"elevenlabs"}),
        )
    )

    result = asyncio.run(activity(_payload(episode)))

    assert result["candidate_id"] == episode.segments[0].render_request.candidate_id
    assert renderer.calls
