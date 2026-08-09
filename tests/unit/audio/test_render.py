"""Tests for deterministic local rendering and durable replay orchestration."""

import asyncio
import wave
from dataclasses import replace
from decimal import Decimal
from io import BytesIO

import pytest

from poddown.audio.contracts import RenderedAudio, RenderRequest
from poddown.audio.local import DeterministicLocalRenderer
from poddown.audio.render import DurableRenderService, RenderRejectedError
from poddown.audio.rights import RightsDeniedError, VoiceConsent
from poddown.audio.storage import (
    ArtifactIntegrityError,
    FilesystemArtifactStore,
    FilesystemRenderRecordStore,
)
from poddown.domain import ProviderUsage


def request(**overrides: object) -> RenderRequest:
    """Build a valid immutable local render request."""
    values: dict[str, object] = {
        "episode_id": "episode-1",
        "episode_version": "v1",
        "segment_id": "segment-1",
        "speaker_id": "host",
        "expected_spoken_text": "A deterministic audio segment.",
        "voice_asset_id": "voice-host-v1",
        "provider": "local",
        "model": "local-deterministic-v1",
    }
    values.update(overrides)
    return RenderRequest(**values)  # type: ignore[arg-type]


def consent() -> VoiceConsent:
    """Build rights evidence matching the local request."""
    return VoiceConsent("voice-host-v1", "consent-1", frozenset({"local"}))


def service(tmp_path) -> tuple[DurableRenderService, DeterministicLocalRenderer]:
    """Build isolated stores and a renderer for an orchestration test."""
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    records = FilesystemRenderRecordStore(tmp_path / "records")
    return DurableRenderService(artifacts, records), DeterministicLocalRenderer()


def render(
    service: DurableRenderService,
    renderer: DeterministicLocalRenderer,
    *,
    take_count: int = 1,
):
    """Run the asynchronous durable service from synchronous tests."""
    return asyncio.run(
        service.render_takes(request(), consent(), renderer, take_count=take_count)
    )


def test_local_renderer_returns_deterministic_valid_wav_with_explicit_usage():
    renderer = DeterministicLocalRenderer()
    rendered = asyncio.run(renderer.render(request()))

    with wave.open(BytesIO(rendered.audio_bytes), "rb") as wav:
        assert wav.getframerate() == 44_100
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getnframes() > 0
    assert rendered.provider == "local"
    assert rendered.model == "local-deterministic-v1"
    assert rendered.output_format == "wav"
    assert rendered.sample_rate_hz == 44_100
    assert rendered.usage == ProviderUsage(
        len(request().expected_spoken_text), len(rendered.audio_bytes)
    )
    assert rendered.cost == Decimal("0")


def test_service_expands_three_takes_with_distinct_immutable_identities(tmp_path):
    durable_service, renderer = service(tmp_path)

    outcomes = render(durable_service, renderer, take_count=3)

    assert len(outcomes) == 3
    assert [outcome.candidate.take_index for outcome in outcomes] == [0, 1, 2]
    assert len({outcome.candidate.candidate_id for outcome in outcomes}) == 3
    assert len({outcome.candidate.artifact.sha256 for outcome in outcomes}) == 3
    assert all(
        outcome.cost_event is not None and not outcome.replayed for outcome in outcomes
    )


def test_service_rejects_rights_before_renderer_dispatch(tmp_path):
    durable_service, renderer = service(tmp_path)

    with pytest.raises(RightsDeniedError):
        asyncio.run(durable_service.render_takes(request(), None, renderer))

    assert renderer.calls == []


def test_service_rejects_capability_before_renderer_dispatch(tmp_path):
    durable_service, renderer = service(tmp_path)
    renderer.capabilities = replace(renderer.capabilities, voice_pinning=False)

    with pytest.raises(RenderRejectedError, match="voice pinning"):
        render(durable_service, renderer)

    assert renderer.calls == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider", "other"),
        ("model", "other-model"),
        ("output_format", "other-format"),
        ("sample_rate_hz", 22_050),
        ("usage", ProviderUsage(1, 1)),
        ("cost", Decimal("1")),
    ],
)
def test_service_rejects_mismatched_renderer_metadata(tmp_path, field, value):
    durable_service, renderer = service(tmp_path)
    original = renderer.render

    async def mismatched(render_request: RenderRequest) -> RenderedAudio:
        valid = await original(render_request)
        rendered = object.__new__(RenderedAudio)
        for attribute in (
            "audio_bytes",
            "provider",
            "model",
            "request_id",
            "usage",
            "cost",
            "output_format",
            "sample_rate_hz",
        ):
            object.__setattr__(rendered, attribute, getattr(valid, attribute))
        object.__setattr__(rendered, field, value)
        return rendered

    renderer.render = mismatched  # type: ignore[method-assign]

    with pytest.raises(RenderRejectedError):
        render(durable_service, renderer)


def test_service_rejects_empty_audio_from_renderer(tmp_path):
    durable_service, renderer = service(tmp_path)

    async def empty(_request: RenderRequest) -> RenderedAudio:
        rendered = object.__new__(RenderedAudio)
        object.__setattr__(rendered, "audio_bytes", b"")
        object.__setattr__(rendered, "provider", "local")
        object.__setattr__(rendered, "model", "local-deterministic-v1")
        object.__setattr__(rendered, "request_id", "request-1")
        object.__setattr__(rendered, "usage", ProviderUsage(1, 1))
        object.__setattr__(rendered, "cost", Decimal("0"))
        object.__setattr__(rendered, "output_format", "wav")
        object.__setattr__(rendered, "sample_rate_hz", 44_100)
        return rendered

    renderer.render = empty  # type: ignore[method-assign]
    with pytest.raises(RenderRejectedError):
        render(durable_service, renderer)


def test_service_replays_persisted_outcome_without_dispatch_or_new_cost_event(tmp_path):
    durable_service, renderer = service(tmp_path)

    first = render(durable_service, renderer)
    second = render(durable_service, renderer)

    assert len(renderer.calls) == 1
    assert first[0].cost_event is not None
    assert second[0].replayed is True
    assert second[0].cost_event is None
    assert second[0].candidate == first[0].candidate


def test_service_dispatches_only_missing_take_during_partial_replay(tmp_path):
    durable_service, renderer = service(tmp_path)
    render(durable_service, renderer, take_count=1)

    outcomes = render(durable_service, renderer, take_count=2)

    assert len(renderer.calls) == 2
    assert outcomes[0].replayed is True
    assert outcomes[0].cost_event is None
    assert outcomes[1].replayed is False
    assert outcomes[1].cost_event is not None


def test_service_fails_closed_for_corrupt_replay_artifact_without_replacement(tmp_path):
    durable_service, renderer = service(tmp_path)
    (outcome,) = render(durable_service, renderer)
    artifact = outcome.candidate.artifact
    artifact_path = tmp_path / "artifacts" / artifact.relative_path
    artifact_path.write_bytes(b"corrupt")

    with pytest.raises(ArtifactIntegrityError):
        render(durable_service, renderer)

    assert len(renderer.calls) == 1
