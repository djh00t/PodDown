"""Concurrent durable-render claim evidence."""

import asyncio

from poddown.audio.contracts import RenderedAudio, RenderRequest
from poddown.audio.local import DeterministicLocalRenderer
from poddown.audio.render import DurableRenderService
from poddown.audio.rights import VoiceConsent
from poddown.audio.storage import FilesystemArtifactStore, FilesystemRenderRecordStore


class YieldingLocalRenderer:
    """Yield after dispatch so two service instances can exercise the race."""

    capabilities = DeterministicLocalRenderer.capabilities

    def __init__(self) -> None:
        self._delegate = DeterministicLocalRenderer()
        self.calls: list[str] = []

    async def render(self, request: RenderRequest) -> RenderedAudio:
        self.calls.append(request.idempotency_key)
        await asyncio.sleep(0)
        return await self._delegate.render(request)


def test_concurrent_identical_requests_claim_one_provider_dispatch(tmp_path):
    """Shared stores must serialize one idempotency key across services."""
    asyncio.run(_run_concurrent_render_claim(tmp_path))


async def _run_concurrent_render_claim(tmp_path) -> None:
    request = RenderRequest(
        episode_id="concurrent-render-episode",
        episode_version="v1",
        segment_id="segment-1",
        speaker_id="host",
        expected_spoken_text="Concurrent durable rendering claims one key.",
        voice_asset_id="voice-host-v1",
        provider="local",
        model="local-deterministic-v1",
    )
    consent = VoiceConsent(
        "voice-host-v1", "consent-concurrent-1", frozenset({"local"})
    )
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    records_a = FilesystemRenderRecordStore(tmp_path / "records", artifacts)
    records_b = FilesystemRenderRecordStore(tmp_path / "records", artifacts)
    service_a = DurableRenderService(artifacts, records_a)
    service_b = DurableRenderService(artifacts, records_b)
    renderer = YieldingLocalRenderer()

    first, second = await asyncio.gather(
        service_a.render_takes(request, consent, renderer),
        service_b.render_takes(request, consent, renderer),
    )
    outcomes = first + second

    assert len(renderer.calls) == 1
    assert sorted(outcome.replayed for outcome in outcomes) == [False, True]
    record = records_a.find(request.idempotency_key)
    assert record is not None
    assert record.cost_event is not None
    assert len(list((tmp_path / "records").rglob("*.json"))) == 1
