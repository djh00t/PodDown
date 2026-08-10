"""Focused behavior tests for the host-local speech renderer."""

import asyncio
import os
import wave
from io import BytesIO
from pathlib import Path

import pytest

from poddown.audio.contracts import RenderRequest
from poddown.audio.render import DurableRenderService
from poddown.audio.rights import VoiceConsent
from poddown.audio.speech import LocalSpeechError, LocalSpeechRenderer
from poddown.audio.storage import FilesystemArtifactStore, FilesystemRenderRecordStore


def wav_bytes(
    *, frames: int = 4_410, sample: int = 1_000, sample_width: int = 2
) -> bytes:
    """Return a low-amplitude canonical PCM WAV fixture."""
    stream = BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(sample_width)
        output.setframerate(44_100)
        payload = (
            bytes((128,)) * frames
            if sample_width == 1
            else sample.to_bytes(sample_width, "little", signed=True) * frames
        )
        output.writeframes(payload)
    return stream.getvalue()


class FakeSpeechRunner:
    """Inject controlled process results while preserving real renderer behavior."""

    def __init__(self, output: bytes | None = None) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.output = wav_bytes() if output is None else output

    def run(
        self, command: tuple[str, ...], *, cwd: Path, timeout_seconds: float
    ) -> None:
        self.commands.append(command)
        assert cwd.is_dir()
        assert timeout_seconds == 120.0
        if command[0].endswith("ffmpeg"):
            Path(command[-1]).write_bytes(self.output)


def request(**overrides: object) -> RenderRequest:
    values: dict[str, object] = {
        "episode_id": "episode-1",
        "episode_version": "v1",
        "segment_id": "segment-1",
        "speaker_id": "host",
        "expected_spoken_text": "Safe local speech text.",
        "voice_asset_id": "voice-host-v1",
        "provider": "host-local",
        "model": "host-local-tts-v1",
    }
    values.update(overrides)
    return RenderRequest(**values)  # type: ignore[arg-type]


def renderer(
    monkeypatch, *, engine: str = "say", runner: FakeSpeechRunner | None = None
):
    import poddown.audio.speech as speech

    monkeypatch.setattr(
        speech.shutil, "which", lambda executable: f"/tools/{executable}"
    )
    return LocalSpeechRenderer(
        engine=engine, process_runner=runner or FakeSpeechRunner()
    )


def test_say_uses_safe_explicit_argv_and_normalizes_to_canonical_wav(monkeypatch):
    fake = FakeSpeechRunner()
    rendered = asyncio.run(
        renderer(monkeypatch, runner=fake).render(
            request(expected_spoken_text="Words; never a shell command.")
        )
    )

    say_command, ffmpeg_command = fake.commands
    assert say_command[0] == "/tools/say"
    assert say_command[1:4] == ("-v", "host", "-o")
    assert say_command[-1] == "Words; never a shell command."
    assert ffmpeg_command[:8] == (
        "/tools/ffmpeg",
        "-y",
        "-i",
        say_command[4],
        "-ac",
        "1",
        "-ar",
        "44100",
    )
    assert ffmpeg_command[8:10] == ("-c:a", "pcm_s16le")
    assert rendered.sample_rate_hz == 44_100
    assert rendered.cost == 0


@pytest.mark.parametrize(
    "engine, executable", [("espeak-ng", "espeak-ng"), ("espeak", "espeak")]
)
def test_espeak_variants_use_safe_explicit_argv(monkeypatch, engine, executable):
    fake = FakeSpeechRunner()
    asyncio.run(renderer(monkeypatch, engine=engine, runner=fake).render(request()))

    command = fake.commands[0]
    assert command[0] == f"/tools/{executable}"
    assert command[1:4] == ("-v", "host", "-w")
    assert command[-1] == "Safe local speech text."


@pytest.mark.parametrize(
    "output, message",
    [
        (b"not a wav", "invalid normalized WAV"),
        (wav_bytes(frames=4_409), "below the minimum"),
    ],
)
def test_renderer_rejects_malformed_or_short_normalized_output(
    monkeypatch, output, message
):
    with pytest.raises(LocalSpeechError, match=message):
        asyncio.run(
            renderer(monkeypatch, runner=FakeSpeechRunner(output)).render(request())
        )


def test_renderer_rejects_clipped_normalized_output(monkeypatch):
    with pytest.raises(LocalSpeechError, match="clipping"):
        asyncio.run(
            renderer(
                monkeypatch, runner=FakeSpeechRunner(wav_bytes(sample=32_767))
            ).render(request())
        )


def test_renderer_rejects_non_16_bit_normalized_output(monkeypatch):
    with pytest.raises(LocalSpeechError, match="sample width"):
        asyncio.run(
            renderer(
                monkeypatch, runner=FakeSpeechRunner(wav_bytes(sample_width=1))
            ).render(request())
        )


def test_renderer_is_accepted_by_durable_preflight(monkeypatch, tmp_path):
    local_renderer = renderer(monkeypatch)
    service = DurableRenderService(
        FilesystemArtifactStore(tmp_path / "artifacts"),
        FilesystemRenderRecordStore(tmp_path / "records"),
    )
    consent = VoiceConsent("voice-host-v1", "consent-1", frozenset({"host-local"}))

    outcomes = asyncio.run(service.render_takes(request(), consent, local_renderer))

    assert len(outcomes) == 1
    assert outcomes[0].candidate.provider == "host-local"


def test_renderer_fails_closed_when_requested_executable_is_missing(monkeypatch):
    import poddown.audio.speech as speech

    monkeypatch.setattr(speech.shutil, "which", lambda executable: None)
    with pytest.raises(LocalSpeechError, match="executable unavailable: say"):
        LocalSpeechRenderer(engine="say", process_runner=FakeSpeechRunner())


def test_renderer_does_not_read_environment_credentials(monkeypatch):
    def fail_on_environment_read(key: str, default: object = None) -> object:
        raise AssertionError(f"environment access is forbidden: {key}")

    monkeypatch.setattr(os, "getenv", fail_on_environment_read)
    fake = FakeSpeechRunner()
    asyncio.run(renderer(monkeypatch, runner=fake).render(request()))
    assert fake.commands


def test_cache_reuses_audio_only_for_same_speaker_voice_text_and_sample_rate(
    monkeypatch,
):
    fake = FakeSpeechRunner()
    local_renderer = renderer(monkeypatch, runner=fake)
    first = asyncio.run(local_renderer.render(request(take_index=0)))
    second = asyncio.run(local_renderer.render(request(take_index=1)))
    changed_voice = asyncio.run(
        local_renderer.render(request(voice_asset_id="voice-host-v2"))
    )

    assert len(fake.commands) == 4
    assert first.audio_bytes == second.audio_bytes
    assert first.request_id != second.request_id
    assert changed_voice.request_id != second.request_id
    assert local_renderer.provenance() == {
        "engine": "say",
        "executable": "/tools/say",
        "ffmpeg_executable": "/tools/ffmpeg",
        "mode": "local-system-tts-demo",
        "voices": {"host": "host"},
    }
