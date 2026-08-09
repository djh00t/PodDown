"""Replay-safe orchestration for immutable audio render candidates."""

import asyncio
import fcntl
import os
import wave
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from io import BytesIO

from poddown.audio.contracts import (
    AudioRenderer,
    ProviderCostEvent,
    RenderCandidate,
    RenderedAudio,
    RenderOutcome,
    RenderRequest,
)
from poddown.audio.rights import VoiceConsent, require_render_rights
from poddown.audio.storage import (
    ArtifactIntegrityError,
    FilesystemArtifactStore,
    FilesystemRenderRecordStore,
)


class RenderRejectedError(ValueError):
    """Raised when a provider cannot safely satisfy a render request."""


class DurableRenderService:
    """Persist immutable render evidence and replay it before new dispatch."""

    def __init__(
        self,
        artifacts: FilesystemArtifactStore,
        records: FilesystemRenderRecordStore,
    ) -> None:
        self._artifacts = artifacts
        self._records = records

    async def render_takes(
        self,
        request: RenderRequest,
        consent: VoiceConsent | None,
        renderer: AudioRenderer,
        *,
        take_count: int = 1,
    ) -> tuple[RenderOutcome, ...]:
        """Render up to three takes, replaying verified evidence when available."""
        if type(take_count) is not int or not 1 <= take_count <= 3:
            raise RenderRejectedError("take_count must be between 1 and 3")
        require_render_rights(request, consent)
        self._require_capabilities(request, renderer)
        requests = tuple(
            replace(request, take_index=request.take_index + offset)
            for offset in range(take_count)
        )

        async with AsyncExitStack() as claims:
            for item in sorted(requests, key=lambda value: value.idempotency_key):
                await claims.enter_async_context(self._claim(item.idempotency_key))
            existing = tuple(
                self._records.find(item.idempotency_key) for item in requests
            )
            for item, stored in zip(requests, existing, strict=True):
                if stored is not None:
                    self._validate_persisted_outcome(item, stored)
            outcomes: list[RenderOutcome] = []
            for item, stored in zip(requests, existing, strict=True):
                if stored is None:
                    outcomes.append(await self._new_outcome(item, renderer))
                else:
                    outcomes.append(
                        RenderOutcome(
                            candidate=stored.candidate,
                            cost_event=None,
                            replayed=True,
                        )
                    )
            return tuple(outcomes)

    @asynccontextmanager
    async def _claim(self, idempotency_key: str) -> AsyncIterator[None]:
        """Serialize one key across async tasks and worker processes."""
        lock_path = self._records.lock_path_for(idempotency_key)
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            await asyncio.to_thread(fcntl.flock, descriptor, fcntl.LOCK_EX)
            yield
        finally:
            try:
                await asyncio.to_thread(fcntl.flock, descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    @staticmethod
    def _require_capabilities(request: RenderRequest, renderer: AudioRenderer) -> None:
        capabilities = renderer.capabilities
        if request.output_format not in capabilities.formats:
            raise RenderRejectedError("requested output format is unavailable")
        if request.sample_rate_hz not in capabilities.sample_rates:
            raise RenderRejectedError("requested sample rate is unavailable")
        if len(request.expected_spoken_text) > capabilities.max_text_characters:
            raise RenderRejectedError("requested text exceeds provider limit")
        if not capabilities.model_pinning:
            raise RenderRejectedError("provider does not support model pinning")
        if not capabilities.voice_pinning:
            raise RenderRejectedError("provider does not support voice pinning")
        if capabilities.timestamps:
            raise RenderRejectedError("provider timestamps must be disabled")
        if not capabilities.provider_idempotency:
            raise RenderRejectedError("provider idempotency is required")

    async def _new_outcome(
        self, request: RenderRequest, renderer: AudioRenderer
    ) -> RenderOutcome:
        rendered = await renderer.render(request)
        self._validate_rendered(request, rendered)
        artifact = self._artifacts.put(rendered.audio_bytes, media_type="audio/wav")
        candidate = RenderCandidate(
            candidate_id=request.candidate_id,
            idempotency_key=request.idempotency_key,
            segment_id=request.segment_id,
            speaker_id=request.speaker_id,
            attempt=request.attempt,
            take_index=request.take_index,
            voice_asset_id=request.voice_asset_id,
            expected_spoken_text=request.expected_spoken_text,
            provider=rendered.provider,
            model=rendered.model,
            request_id=rendered.request_id,
            usage=rendered.usage,
            cost=rendered.cost,
            artifact=artifact,
        )
        outcome = RenderOutcome(
            candidate=candidate,
            cost_event=ProviderCostEvent(
                event_id=f"cost-{candidate.candidate_id}",
                candidate_id=candidate.candidate_id,
                provider=candidate.provider,
                usage=candidate.usage,
                cost=candidate.cost,
            ),
            replayed=False,
        )
        self._records.save(outcome)
        return outcome

    @staticmethod
    def _validate_rendered(request: RenderRequest, rendered: RenderedAudio) -> None:
        if not isinstance(rendered.audio_bytes, bytes) or not rendered.audio_bytes:
            raise RenderRejectedError("renderer returned empty audio bytes")
        if rendered.provider != request.provider:
            raise RenderRejectedError("renderer provider does not match request")
        if rendered.model != request.model:
            raise RenderRejectedError("renderer model does not match request")
        if rendered.output_format != request.output_format:
            raise RenderRejectedError("renderer format does not match request")
        if rendered.sample_rate_hz != request.sample_rate_hz:
            raise RenderRejectedError("renderer sample rate does not match request")
        if rendered.usage.input_units != len(request.expected_spoken_text):
            raise RenderRejectedError("renderer input usage does not match request")
        if rendered.usage.output_units != len(rendered.audio_bytes):
            raise RenderRejectedError("renderer output usage does not match audio")
        if request.provider == "local" and not rendered.cost.is_zero():
            raise RenderRejectedError("local renderer cost must be zero")
        if request.output_format == "wav":
            DurableRenderService._validate_wav(
                rendered.audio_bytes, request.sample_rate_hz
            )

    @staticmethod
    def _validate_wav(audio_bytes: bytes, sample_rate_hz: int) -> None:
        """Require complete mono 16-bit PCM frames in a valid WAV container."""
        try:
            with wave.open(BytesIO(audio_bytes), "rb") as audio:
                channels = audio.getnchannels()
                sample_width = audio.getsampwidth()
                frame_rate = audio.getframerate()
                frame_count = audio.getnframes()
                if (
                    audio.getcomptype() != "NONE"
                    or channels != 1
                    or sample_width != 2
                    or frame_rate != sample_rate_hz
                    or frame_count <= 0
                ):
                    raise RenderRejectedError("renderer returned invalid WAV metadata")
                frame_bytes = audio.readframes(frame_count)
                if len(frame_bytes) != frame_count * channels * sample_width:
                    raise RenderRejectedError("renderer returned truncated WAV frames")
        except (EOFError, OSError, ValueError, wave.Error) as error:
            raise RenderRejectedError("renderer returned invalid WAV audio") from error

    @staticmethod
    def _validate_persisted_outcome(
        request: RenderRequest, outcome: RenderOutcome
    ) -> None:
        """Authenticate persisted evidence against the request lookup boundary."""
        candidate = outcome.candidate
        expected_fields = {
            "segment_id": request.segment_id,
            "speaker_id": request.speaker_id,
            "attempt": request.attempt,
            "take_index": request.take_index,
            "voice_asset_id": request.voice_asset_id,
            "expected_spoken_text": request.expected_spoken_text,
            "provider": request.provider,
            "model": request.model,
        }
        if candidate.idempotency_key != request.idempotency_key:
            raise ArtifactIntegrityError(
                "persisted idempotency key does not match request"
            )
        if candidate.candidate_id != request.candidate_id:
            raise ArtifactIntegrityError(
                "persisted candidate identity does not match request"
            )
        for field, expected in expected_fields.items():
            if getattr(candidate, field) != expected:
                raise ArtifactIntegrityError(
                    f"persisted candidate {field} does not match request"
                )
        if outcome.replayed:
            raise ArtifactIntegrityError("persisted outcome cannot already be replayed")
        cost_event = outcome.cost_event
        if cost_event is None:
            raise ArtifactIntegrityError("persisted outcome is missing cost evidence")
        if cost_event.event_id != f"cost-{candidate.candidate_id}":
            raise ArtifactIntegrityError("persisted cost event identity is invalid")
        if cost_event.candidate_id != candidate.candidate_id:
            raise ArtifactIntegrityError("persisted cost event candidate is invalid")
        if cost_event.provider != candidate.provider:
            raise ArtifactIntegrityError("persisted cost event provider is invalid")
        if cost_event.usage != candidate.usage:
            raise ArtifactIntegrityError("persisted cost event usage is invalid")
        if cost_event.cost != candidate.cost:
            raise ArtifactIntegrityError("persisted cost event cost is invalid")
