"""Regression coverage for deterministic local renderer output quality."""

import asyncio

from poddown.audio.contracts import RenderRequest
from poddown.audio.diagnostics import diagnose_wav
from poddown.audio.local import DeterministicLocalRenderer


def test_deterministic_renderer_never_generates_clipped_pcm():
    """Local demo audio must satisfy the same hard clipping gate as providers."""
    request = RenderRequest(
        episode_id="temporal-activity-episode",
        episode_version="v1",
        segment_id="segment-1",
        speaker_id="host",
        expected_spoken_text="Temporal local activity renders durable audio.",
        voice_asset_id="voice-host-v1",
        provider="local",
        model="local-deterministic-v1",
    )

    rendered = asyncio.run(DeterministicLocalRenderer().render(request))
    diagnostics = diagnose_wav(
        rendered.audio_bytes,
        expected_sample_rate_hz=request.sample_rate_hz,
        expected_channels=1,
    )

    assert diagnostics.clipping_ratio == 0.0
    assert diagnostics.passes_hard_gates is True
