"""BDD bindings for the ElevenLabs durable-audio renderer adapter."""

from __future__ import annotations

import asyncio
from decimal import Decimal

from pytest_bdd import given, scenarios, then, when

from poddown.audio.contracts import RenderRequest
from poddown.audio.elevenlabs import ElevenLabsAudioRenderer
from poddown.domain import ProviderUsage
from poddown.providers.elevenlabs_client import ElevenLabsSynthesisResult
from tests.contract.providers.helpers import mono_pcm_wave

scenarios("../features/elevenlabs_durable_renderer.feature")


class _Client:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def synthesize(self, text: str) -> ElevenLabsSynthesisResult:
        self.texts.append(text)
        audio = mono_pcm_wave(44_100)
        return ElevenLabsSynthesisResult(
            audio_bytes=audio,
            request_id="eleven-request-1",
            model="eleven-multilingual-v2",
            voice_id="voice-host-v1",
            usage=ProviderUsage(len(text), len(audio)),
            estimated_cost=Decimal("0.003"),
        )


@given("an injected ElevenLabs synthesis client")
def injected_client(context) -> None:
    client = _Client()
    request = RenderRequest(
        episode_id="episode-1",
        episode_version="v1",
        segment_id="segment-1",
        speaker_id="host",
        expected_spoken_text="The system is source-bound.",
        voice_asset_id="voice-host-v1",
        provider="elevenlabs",
        model="eleven-multilingual-v2",
        sample_rate_hz=44_100,
    )
    context.values.update(
        client=client,
        request=request,
        renderer=ElevenLabsAudioRenderer(client),
    )


@when("I render one ElevenLabs request through the durable adapter")
def render_request(context) -> None:
    context.values["rendered"] = asyncio.run(
        context.values["renderer"].render(context.values["request"])
    )


@then("the renderer preserves the request text and provider metadata")
def normalized_render(context) -> None:
    request = context.values["request"]
    rendered = context.values["rendered"]
    assert context.values["client"].texts == [request.expected_spoken_text]
    assert rendered.provider == "elevenlabs"
    assert rendered.model == request.model
    assert rendered.request_id == "eleven-request-1"
    assert rendered.usage == ProviderUsage(
        len(request.expected_spoken_text), len(rendered.audio_bytes)
    )
    assert rendered.cost == Decimal("0.003")
