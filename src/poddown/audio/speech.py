"""Host-local speech rendering behind an injectable process boundary."""

from __future__ import annotations

import platform
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Protocol
from uuid import uuid4

from poddown.audio.contracts import RenderedAudio, RenderRequest
from poddown.audio.diagnostics import AudioDiagnosticsError, diagnose_wav
from poddown.domain import ProviderUsage
from poddown.providers.contracts import ProviderCapabilities

_TIMEOUT_SECONDS = 120.0
_MIN_DURATION_SECONDS = 0.1


class LocalSpeechError(ValueError):
    """Raised when host-local speech cannot produce verified WAV audio."""


class SpeechProcessRunner(Protocol):
    """Injectable subprocess boundary for host-local speech commands."""

    def run(
        self, command: tuple[str, ...], *, cwd: Path, timeout_seconds: float
    ) -> None:
        """Run one argv-only command without returning process output."""


class SubprocessSpeechRunner:
    """Run a local speech process while keeping its output out of application logs."""

    def run(
        self, command: tuple[str, ...], *, cwd: Path, timeout_seconds: float
    ) -> None:
        """Run one checked command with bounded execution time."""
        subprocess.run(
            command,
            cwd=cwd,
            check=True,
            capture_output=True,
            timeout=timeout_seconds,
        )


class LocalSpeechRenderer:
    """Render speech through a host TTS executable and canonical ffmpeg WAV output."""

    provider = "host-local"
    model = "host-local-tts-v1"
    mode = "local-system-tts-demo"
    capabilities = ProviderCapabilities(
        formats=frozenset({"wav"}),
        sample_rates=frozenset({44_100}),
        max_text_characters=100_000,
        model_pinning=True,
        voice_pinning=True,
        timestamps=False,
        provider_idempotency=False,
    )

    def __init__(
        self,
        engine: str = "auto",
        voices: Mapping[str, str] | None = None,
        process_runner: SpeechProcessRunner | None = None,
        ffmpeg_executable: str = "ffmpeg",
    ) -> None:
        self._engine, self._executable = self._resolve_engine(engine)
        ffmpeg_path = shutil.which(ffmpeg_executable)
        if ffmpeg_path is None:
            raise LocalSpeechError(
                f"local speech executable unavailable: {ffmpeg_executable}"
            )
        self._ffmpeg_executable = ffmpeg_path
        self._voices = dict(voices or {})
        self._process_runner = process_runner or SubprocessSpeechRunner()
        self._cache: dict[tuple[str, str, str, int], bytes] = {}

    def provenance(self) -> Mapping[str, object]:
        """Return the local executable and voice bindings used for rendering."""
        return MappingProxyType(
            {
                "engine": self._engine,
                "executable": self._executable,
                "ffmpeg_executable": self._ffmpeg_executable,
                "mode": self.mode,
                "voices": dict(self._voices),
            }
        )

    async def render(self, request: RenderRequest) -> RenderedAudio:
        """Render one request to validated canonical PCM WAV bytes."""
        cache_key = (
            request.speaker_id,
            request.voice_asset_id,
            request.expected_spoken_text,
            request.sample_rate_hz,
        )
        audio_bytes = self._cache.get(cache_key)
        if audio_bytes is None:
            audio_bytes = self._render_normalized_wav(request)
            self._cache[cache_key] = audio_bytes
        return RenderedAudio(
            audio_bytes=audio_bytes,
            provider=request.provider,
            model=request.model,
            request_id=f"host-local-{uuid4()}",
            usage=ProviderUsage(
                input_units=len(request.expected_spoken_text),
                output_units=len(audio_bytes),
            ),
            cost=Decimal("0"),
            output_format="wav",
            sample_rate_hz=44_100,
        )

    def _resolve_engine(self, engine: str) -> tuple[str, str]:
        if engine == "auto":
            candidates = (
                ("say",) if platform.system() == "Darwin" else ("espeak-ng", "espeak")
            )
        elif engine in {"say", "espeak-ng", "espeak"}:
            candidates = (engine,)
        else:
            raise LocalSpeechError(f"unsupported local speech engine: {engine}")
        for candidate in candidates:
            executable = shutil.which(candidate)
            if executable is not None:
                return candidate, executable
        raise LocalSpeechError(f"local speech executable unavailable: {candidates[0]}")

    def _render_normalized_wav(self, request: RenderRequest) -> bytes:
        voice = self._voices.setdefault(request.speaker_id, request.speaker_id)
        with tempfile.TemporaryDirectory(prefix="poddown-speech-") as temporary:
            cwd = Path(temporary)
            native = cwd / ("speech.aiff" if self._engine == "say" else "speech.wav")
            normalized = cwd / "normalized.wav"
            self._run_stage(
                self._engine_command(voice, native, request.expected_spoken_text),
                cwd,
                "engine",
            )
            self._run_stage(
                (
                    self._ffmpeg_executable,
                    "-y",
                    "-i",
                    str(native),
                    "-ac",
                    "1",
                    "-ar",
                    "44100",
                    "-c:a",
                    "pcm_s16le",
                    str(normalized),
                ),
                cwd,
                "normalization",
            )
            try:
                audio_bytes = normalized.read_bytes()
            except OSError as error:
                raise LocalSpeechError("local speech normalization failed") from error
        try:
            diagnostics = diagnose_wav(
                audio_bytes,
                expected_sample_rate_hz=44_100,
                expected_channels=1,
                min_duration_seconds=_MIN_DURATION_SECONDS,
            )
        except AudioDiagnosticsError as error:
            raise LocalSpeechError(f"invalid normalized WAV: {error}") from error
        if diagnostics.clipping_ratio != 0.0:
            raise LocalSpeechError("invalid normalized WAV: clipping detected")
        return audio_bytes

    def _engine_command(self, voice: str, output: Path, text: str) -> tuple[str, ...]:
        if self._engine == "say":
            return (self._executable, "-v", voice, "-o", str(output), text)
        return (self._executable, "-v", voice, "-w", str(output), text)

    def _run_stage(self, command: tuple[str, ...], cwd: Path, stage: str) -> None:
        try:
            self._process_runner.run(command, cwd=cwd, timeout_seconds=_TIMEOUT_SECONDS)
        except (OSError, subprocess.SubprocessError) as error:
            raise LocalSpeechError(f"local speech {stage} failed") from error
