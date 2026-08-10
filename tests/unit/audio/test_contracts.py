"""Tests for immutable durable-audio value contracts."""

import asyncio
import inspect
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

import poddown.audio as audio
from poddown.audio import (
    ArtifactRef,
    AudioRenderer,
    ProviderCostEvent,
    RenderCandidate,
    RenderedAudio,
    RenderOutcome,
    RenderRequest,
)
from poddown.domain import ProviderUsage
from poddown.providers.contracts import ProviderCapabilities


def _request(**overrides: object) -> RenderRequest:
    values: dict[str, object] = {
        "episode_id": "episode-001",
        "episode_version": "v1",
        "segment_id": "segment-001",
        "speaker_id": "host",
        "expected_spoken_text": "The rate is 13.9 hertz.",
        "voice_asset_id": "voice-host-v1",
        "provider": "local",
        "model": "local-deterministic-v1",
    }
    values.update(overrides)
    return RenderRequest(**values)  # type: ignore[arg-type]


def _rendered_audio(**overrides: object) -> RenderedAudio:
    values: dict[str, object] = {
        "audio_bytes": b"RIFFfixture",
        "provider": "local",
        "model": "local-deterministic-v1",
        "request_id": "local-request-001",
        "usage": ProviderUsage(input_units=26, output_units=11),
        "cost": Decimal("0"),
        "output_format": "wav",
        "sample_rate_hz": 44_100,
    }
    values.update(overrides)
    return RenderedAudio(**values)  # type: ignore[arg-type]


def _artifact(**overrides: object) -> ArtifactRef:
    values: dict[str, object] = {
        "sha256": "a" * 64,
        "media_type": "audio/wav",
        "size_bytes": 11,
        "relative_path": "artifacts/aa/fixture.wav",
    }
    values.update(overrides)
    return ArtifactRef(**values)  # type: ignore[arg-type]


def _candidate(**overrides: object) -> RenderCandidate:
    values: dict[str, object] = {
        "candidate_id": "candidate-001",
        "idempotency_key": "render-001",
        "segment_id": "segment-001",
        "speaker_id": "host",
        "attempt": 1,
        "take_index": 0,
        "voice_asset_id": "voice-host-v1",
        "expected_spoken_text": "The rate is 13.9 hertz.",
        "provider": "local",
        "model": "local-deterministic-v1",
        "request_id": "local-request-001",
        "usage": ProviderUsage(input_units=26, output_units=11),
        "cost": Decimal("0"),
        "artifact": _artifact(),
    }
    values.update(overrides)
    return RenderCandidate(**values)  # type: ignore[arg-type]


def _capabilities() -> ProviderCapabilities:
    return ProviderCapabilities(
        formats=frozenset({"wav"}),
        sample_rates=frozenset({44_100}),
        max_text_characters=10_000,
        model_pinning=True,
        voice_pinning=True,
        timestamps=False,
        provider_idempotency=True,
    )


def test_equal_requests_have_stable_identities_and_are_frozen():
    """Identity must remain replay-safe for equivalent immutable input."""
    first = _request()
    second = _request()

    assert first == second
    assert first.idempotency_key == second.idempotency_key
    assert first.candidate_id == second.candidate_id
    assert first.candidate_id.startswith("candidate-")
    with pytest.raises(FrozenInstanceError):
        first.attempt = 2  # type: ignore[misc]


@pytest.mark.parametrize("field", ["attempt", "take_index"])
def test_identity_changes_for_a_distinct_render_take(field: str):
    """Attempt and take indexes must keep billed render takes distinct."""
    request = _request()
    changed = _request(**{field: getattr(request, field) + 1})

    assert changed.idempotency_key != request.idempotency_key
    assert changed.candidate_id != request.candidate_id


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("episode_id", ""),
        ("episode_version", ""),
        ("segment_id", ""),
        ("speaker_id", ""),
        ("expected_spoken_text", ""),
        ("voice_asset_id", ""),
        ("provider", ""),
        ("model", ""),
        ("attempt", 0),
        ("attempt", True),
        ("take_index", -1),
        ("take_index", True),
        ("output_format", "mp3"),
        ("sample_rate_hz", 0),
        ("sample_rate_hz", True),
    ],
)
def test_request_rejects_invalid_stable_input(field: str, value: object):
    """Malformed request input must fail before identity or dispatch is possible."""
    with pytest.raises(ValueError):
        _request(**{field: value})


@pytest.mark.parametrize(
    ("usage", "cost"),
    [
        (ProviderUsage(input_units=0, output_units=1), Decimal("0")),
        (ProviderUsage(input_units=1, output_units=0), Decimal("0")),
        (ProviderUsage(input_units=True, output_units=1), Decimal("0")),
        (ProviderUsage(input_units=1, output_units=True), Decimal("0")),
        (ProviderUsage(input_units=1, output_units=1), Decimal("-0.01")),
        (ProviderUsage(input_units=1, output_units=1), Decimal("NaN")),
    ],
)
def test_rendered_audio_rejects_invalid_metering(usage: ProviderUsage, cost: Decimal):
    """Accepted audio needs positive integer usage and a finite non-negative cost."""
    with pytest.raises(ValueError):
        _rendered_audio(usage=usage, cost=cost)


def test_rendered_audio_rejects_empty_bytes_but_not_service_metadata_mismatch():
    """Byte validity belongs here; metadata comparison belongs in the service."""
    with pytest.raises(ValueError):
        _rendered_audio(audio_bytes=b"")

    rendered = _rendered_audio(provider="another-provider", model="another-model")

    assert rendered.provider == "another-provider"
    assert rendered.model == "another-model"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sha256", "not-a-digest"),
        ("size_bytes", -1),
    ],
)
def test_artifact_ref_rejects_invalid_integrity_metadata(field: str, value: object):
    """Artifact references cannot represent malformed content-addressed objects."""
    with pytest.raises(ValueError):
        _artifact(**{field: value})


def test_public_audio_package_exports_all_contract_values():
    """The package facade must expose every contract used by later tasks."""
    assert audio.ArtifactRef is ArtifactRef
    assert audio.ProviderCostEvent is ProviderCostEvent
    assert audio.RenderCandidate is RenderCandidate
    assert audio.RenderOutcome is RenderOutcome
    assert audio.AudioRenderer is AudioRenderer


def test_public_contract_values_are_frozen():
    """Every exported immutable value must reject post-construction mutation."""
    cost_event = ProviderCostEvent(
        event_id="cost-001",
        candidate_id="candidate-001",
        provider="local",
        usage=ProviderUsage(input_units=26, output_units=11),
        cost=Decimal("0"),
    )
    outcome = RenderOutcome(
        candidate=_candidate(), cost_event=cost_event, replayed=False
    )
    values = (
        (_artifact(), "media_type"),
        (cost_event, "provider"),
        (_candidate(), "model"),
        (outcome, "replayed"),
    )

    for value, field in values:
        with pytest.raises(FrozenInstanceError):
            setattr(value, field, None)


@pytest.mark.parametrize(
    ("usage", "cost"),
    [
        (ProviderUsage(input_units=0, output_units=1), Decimal("0")),
        (ProviderUsage(input_units=1, output_units=True), Decimal("0")),
    ],
)
def test_provider_cost_event_rejects_invalid_metering(
    usage: ProviderUsage, cost: Decimal
):
    """Cost events cannot persist non-positive or boolean usage values."""
    with pytest.raises(ValueError):
        ProviderCostEvent("cost-001", "candidate-001", "local", usage, cost)


def test_render_candidate_rejects_an_invalid_artifact_reference():
    """Candidates must point to an immutable artifact reference."""
    with pytest.raises(ValueError, match="artifact"):
        _candidate(artifact="not-an-artifact")


@pytest.mark.parametrize(
    "values",
    [
        {"candidate": "not-a-candidate"},
        {"cost_event": "not-a-cost-event"},
        {"replayed": 1},
    ],
)
def test_render_outcome_rejects_invalid_members(values: dict[str, object]):
    """Outcomes must contain typed candidate, event, and replay state values."""
    defaults: dict[str, object] = {
        "candidate": _candidate(),
        "cost_event": None,
        "replayed": False,
    }
    defaults.update(values)

    with pytest.raises(ValueError):
        RenderOutcome(**defaults)  # type: ignore[arg-type]


class AsyncRenderer:
    """Renderer-shaped implementation for the public async contract test."""

    capabilities = _capabilities()

    async def render(self, request: RenderRequest) -> RenderedAudio:
        assert request == _request()
        return _rendered_audio()


def test_audio_renderer_port_is_async_and_accepts_async_renderer_shape():
    """The durable service can await an implementation of the public renderer port."""
    assert inspect.iscoroutinefunction(AudioRenderer.render)
    renderer: AudioRenderer = AsyncRenderer()

    rendered = asyncio.run(renderer.render(_request()))

    assert rendered.provider == "local"
    assert rendered.model == "local-deterministic-v1"
