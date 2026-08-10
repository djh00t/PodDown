"""Executable acceptance tests for host-local speech rendering."""

import asyncio
import subprocess
import wave
from io import BytesIO
from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when

from poddown.audio.contracts import RenderRequest
from poddown.audio.diagnostics import diagnose_wav
from poddown.audio.speech import LocalSpeechError, LocalSpeechRenderer

scenarios("../features/local_speech.feature")


def _wav_bytes() -> bytes:
    stream = BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(44_100)
        output.writeframes((1_000).to_bytes(2, "little", signed=True) * 4_410)
    return stream.getvalue()


class SpeechRunner:
    """Write a controlled normalized WAV without starting a local TTS engine."""

    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []

    def run(
        self, command: tuple[str, ...], *, cwd: Path, timeout_seconds: float
    ) -> None:
        self.commands.append(command)
        if "ffmpeg" in command[0]:
            Path(command[-1]).write_bytes(_wav_bytes())


def _request(take_index: int = 0) -> RenderRequest:
    return RenderRequest(
        episode_id="demo-episode",
        episode_version="v1",
        segment_id="intro",
        speaker_id="host",
        expected_spoken_text="A local renderer makes this demo listenable.",
        voice_asset_id="host-voice-v1",
        provider="host-local",
        model="host-local-tts-v1",
        take_index=take_index,
    )


@pytest.fixture
def speech_context(monkeypatch):
    import poddown.audio.speech as speech

    monkeypatch.setattr(
        speech.shutil,
        "which",
        lambda executable: f"/fake/{executable}",
    )

    def version_run(command, **_kwargs):
        executable = Path(command[0]).name
        if executable == "sw_vers":
            return subprocess.CompletedProcess(command, 0, stdout="26.6\n")
        return subprocess.CompletedProcess(command, 0, stdout=f"{executable} 1.0\n")

    monkeypatch.setattr(speech.subprocess, "run", version_run)
    runner = SpeechRunner()
    return {"renderer": LocalSpeechRenderer(process_runner=runner), "runner": runner}


@given("an available host-local speech engine")
def available_host_local_speech_engine(speech_context):
    assert speech_context["renderer"].provenance()["engine"] in {"say", "espeak-ng"}


@then("local renderer provenance records resolved tool versions")
def local_renderer_provenance_records_resolved_tool_versions(speech_context):
    provenance = speech_context["renderer"].provenance()
    if provenance["engine"] == "say":
        assert provenance["engine_version"] == "macOS say 26.6"
    else:
        assert provenance["engine_version"] == f"{provenance['engine']} 1.0"
    assert provenance["ffmpeg_version"] == "ffmpeg 1.0"


@given("the requested local speech executable is unavailable")
def unavailable_local_speech_executable(monkeypatch, speech_context):
    import poddown.audio.speech as speech

    monkeypatch.setattr(speech.shutil, "which", lambda executable: None)
    try:
        speech_context["renderer"] = LocalSpeechRenderer(
            engine="say", process_runner=speech_context["runner"]
        )
    except LocalSpeechError as error:
        speech_context["error"] = error


@when("I render a local speech request")
def render_local_speech_request(speech_context):
    if "error" in speech_context:
        return
    renderer = speech_context["renderer"]
    try:
        speech_context["rendered"] = asyncio.run(renderer.render(_request()))
    except LocalSpeechError as error:
        speech_context["error"] = error


@when("I render three takes with the same text and voice")
def render_three_equivalent_takes(speech_context):
    renderer = speech_context["renderer"]
    speech_context["rendered"] = [
        asyncio.run(renderer.render(_request(take_index))) for take_index in range(3)
    ]


@then("I receive canonical zero-cost speech WAV audio")
def canonical_zero_cost_speech_wav(speech_context):
    rendered = speech_context["rendered"]
    engine = speech_context["renderer"].provenance()["engine"]
    engine_command = speech_context["runner"].commands[0]
    diagnostics = diagnose_wav(
        rendered.audio_bytes, expected_sample_rate_hz=44_100, expected_channels=1
    )
    assert engine_command[0] == f"/fake/{engine}"
    assert "-o" in engine_command if engine == "say" else "-w" in engine_command
    assert diagnostics.clipping_ratio == 0.0
    assert rendered.cost == 0
    assert rendered.provider == "host-local"
    assert rendered.model == "host-local-tts-v1"


@then("local speech rendering fails closed")
def local_speech_rendering_fails_closed(speech_context):
    assert str(speech_context["error"]) == "local speech executable unavailable: say"
    assert speech_context["runner"].commands == []


@then("the local engine renders once and each take has a unique request ID")
def equivalent_takes_reuse_normalized_audio(speech_context):
    rendered = speech_context["rendered"]
    engine_commands = [
        command
        for command in speech_context["runner"].commands
        if "ffmpeg" not in command[0]
    ]
    assert len(engine_commands) == 1
    assert len({result.request_id for result in rendered}) == 3
    assert len({result.audio_bytes for result in rendered}) == 1
