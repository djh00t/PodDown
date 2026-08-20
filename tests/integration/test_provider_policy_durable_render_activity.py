"""Integration coverage for V07 policy at the durable activity boundary."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from temporalio.exceptions import ApplicationError

from poddown.audio.activities import (
    ProviderActivityDispatchPolicy,
    build_durable_render_activity,
    deterministic_quality_evaluator,
)
from poddown.audio.contracts import RenderRequest
from poddown.audio.local import DeterministicLocalRenderer
from poddown.audio.render import DurableRenderService
from poddown.audio.rights import VoiceConsent
from poddown.audio.storage import (
    FilesystemArtifactStore,
    FilesystemQualityRecordStore,
    FilesystemRenderRecordStore,
    FilesystemTranscriptionRecordStore,
)
from poddown.audio.workflow import (
    EpisodeWorkflowInput,
    SegmentWorkflowInput,
    activity_key_for,
)
from poddown.provider_routes import ProviderBinding, ProviderRoute
from poddown.providers.contracts import TranscriptResult
from poddown.providers.policy import ProviderDispatchPreflight
from poddown.providers.registry import ProviderRegistration, ProviderRegistry
from poddown.providers.settings import ProviderRuntimeSettings


class NeverCalledRenderer(DeterministicLocalRenderer):
    """Fail if policy allows this fake renderer to dispatch."""

    async def render(self, request: RenderRequest):  # type: ignore[override]
        del request
        raise AssertionError("provider policy must run before renderer dispatch")


class NeverCalledTranscriber:
    """Fail if policy allows this fake transcriber to dispatch."""

    async def transcribe(self, audio: bytes) -> TranscriptResult:
        del audio
        raise AssertionError("provider policy must run before transcription dispatch")


def _policy(
    *, transcription_estimated_cost: Decimal = Decimal("0")
) -> ProviderActivityDispatchPolicy:
    registry = ProviderRegistry()
    registry.register(
        ProviderRegistration(
            provider="local",
            mode="deterministic-local",
            model="local-deterministic-v1",
            capabilities=frozenset({"voice-pinning"}),
            voice_asset_ids=frozenset({"voice-host-v1"}),
        )
    )
    route = ProviderRoute(
        route_id="local-route",
        mode="deterministic-local",
        renderer=ProviderBinding(
            provider="local",
            model="local-deterministic-v1",
            required_capabilities=frozenset({"voice-pinning"}),
            voice_asset_id="voice-host-v1",
        ),
        transcriber=ProviderBinding(
            provider="local",
            model="local-transcriber-v1",
            required_capabilities=frozenset(),
        ),
        fallbacks=(),
        pricing_version="test-v1",
        max_request_cost=Decimal("0"),
        max_episode_cost=Decimal("0"),
    )
    registry.register(
        ProviderRegistration(
            provider="local",
            mode="deterministic-local",
            model="local-transcriber-v1",
            capabilities=frozenset(),
        )
    )
    registry.register_route(ProviderRuntimeSettings(route=route))
    return ProviderActivityDispatchPolicy(
        preflight=ProviderDispatchPreflight(registry),
        route=route,
        render_estimated_cost=Decimal("0"),
        transcription_estimated_cost=transcription_estimated_cost,
    )


def _payload(
    *, allowed_providers: frozenset[str] = frozenset({"other"})
) -> dict[str, object]:
    request = RenderRequest(
        episode_id="policy-activity-episode",
        episode_version="v1",
        segment_id="segment-1",
        speaker_id="host",
        expected_spoken_text="Provider policy protects dispatch.",
        voice_asset_id="voice-host-v1",
        provider="local",
        model="local-deterministic-v1",
    )
    segment = SegmentWorkflowInput(
        segment_id=request.segment_id,
        render_request=request,
        consent=VoiceConsent("voice-host-v1", "consent-1", allowed_providers),
        critical_tokens=("policy",),
    )
    episode = EpisodeWorkflowInput(
        episode_id=request.episode_id,
        episode_version=request.episode_version,
        segments=(segment,),
    )
    return {
        "episode": episode.to_dict(),
        "segment": segment.to_dict(),
        "attempt": 1,
        "take": 0,
        "activity_key": activity_key_for(
            episode, "render", segment.segment_id, attempt=1, take=0
        ),
    }


def test_activity_returns_structured_policy_denial_before_renderer_dispatch(
    tmp_path,
) -> None:
    """Changing preflight order would send an unconsented request to a provider."""
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    records = FilesystemRenderRecordStore(tmp_path / "records", artifacts)
    activity = build_durable_render_activity(
        DurableRenderService(artifacts, records),
        NeverCalledRenderer(),
        artifacts,
        provider_dispatch=_policy(),
    )

    with pytest.raises(ApplicationError) as error:
        asyncio.run(activity(_payload()))

    assert error.value.type == "ProviderDispatchRejectedError"
    assert error.value.non_retryable is True
    assert error.value.details == (["consent_provider_not_allowed"],)


def test_local_activity_with_injected_quality_evaluator_needs_no_transcriber_policy(
    tmp_path,
) -> None:
    """A local evaluator must not be mistaken for a provider transcription call."""
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    records = FilesystemRenderRecordStore(tmp_path / "records", artifacts)

    def local_quality(*args, **kwargs):
        return deterministic_quality_evaluator(*args, **kwargs)

    activity = build_durable_render_activity(
        DurableRenderService(artifacts, records),
        DeterministicLocalRenderer(),
        artifacts,
        quality_evaluator=local_quality,
        provider_dispatch=_policy(transcription_estimated_cost=Decimal("0.01")),
    )

    result = asyncio.run(activity(_payload(allowed_providers=frozenset({"local"}))))

    assert result["transcription"]["mode"] == "deterministic-local"


def test_activity_returns_structured_policy_denial_before_transcriber_dispatch(
    tmp_path,
) -> None:
    """Changing transcription preflight order would send audio to a provider."""
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    records = FilesystemRenderRecordStore(tmp_path / "records", artifacts)
    activity = build_durable_render_activity(
        DurableRenderService(artifacts, records),
        DeterministicLocalRenderer(),
        artifacts,
        transcriber=NeverCalledTranscriber(),
        quality_records=FilesystemQualityRecordStore(tmp_path / "quality"),
        transcription_records=FilesystemTranscriptionRecordStore(
            tmp_path / "transcriptions"
        ),
        provider_dispatch=_policy(transcription_estimated_cost=Decimal("0.01")),
    )

    with pytest.raises(ApplicationError) as error:
        asyncio.run(activity(_payload(allowed_providers=frozenset({"local"}))))

    assert error.value.type == "ProviderDispatchRejectedError"
    assert error.value.details == (
        ["request_budget_exceeded", "episode_budget_exceeded"],
    )
