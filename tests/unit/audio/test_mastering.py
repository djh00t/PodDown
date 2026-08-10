"""Focused unit coverage for the public mastering API."""

import shutil
import subprocess
import wave
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

import pytest

from poddown.audio.mastering import (
    FfmpegResult,
    MasteringError,
    MasteringProfile,
    MasteringRequest,
    MasteringSegment,
    MasteringService,
    SubprocessFfmpegRunner,
    SubprocessMp3Inspector,
)


def wav_bytes(
    *,
    samples: tuple[int, ...] = (1_000,) * 882,
    sample_rate: int = 44_100,
    channels: int = 1,
    sample_width: int = 2,
) -> bytes:
    """Build a short mono PCM WAV fixture using only the standard library."""
    stream = BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(sample_width)
        output.setframerate(sample_rate)
        payload = bytearray()
        for sample in samples:
            if sample_width == 1:
                frame = bytes((max(0, min(255, sample + 128)),))
            else:
                frame = sample.to_bytes(sample_width, "little", signed=True)
            payload.extend(frame * channels)
        output.writeframes(bytes(payload))
    return stream.getvalue()


PROFILE = MasteringProfile(max_duration_seconds=10.0)
SEGMENT_WAV = wav_bytes()
MASTERED_MP3 = b"ID3fake-mastered-mp3"


def segment(position: int = 1, audio_bytes: bytes = SEGMENT_WAV) -> MasteringSegment:
    """Build a valid mastering segment, allowing one focused override."""
    return MasteringSegment(position, f"segment-{position}", audio_bytes)


def request(
    *segments: MasteringSegment, profile: MasteringProfile = PROFILE
) -> MasteringRequest:
    """Build a valid mastering request for the supplied segments."""
    return MasteringRequest("episode-1", "v1", segments, profile)


@dataclass
class FakeRunner:
    """Injected runner that records normalized service dispatch."""

    result: FfmpegResult
    calls: list[tuple[MasteringSegment, ...]] = field(default_factory=list)

    def run(
        self,
        *,
        segments: tuple[MasteringSegment, ...],
        profile: MasteringProfile,
    ) -> FfmpegResult:
        """Return a configured result after recording the received segments."""
        self.calls.append(segments)
        return self.result


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"sample_rate_hz": 0}, "sample rate"),
        ({"channels": 2}, "mono"),
        (
            {"min_duration_seconds": 2.0, "max_duration_seconds": 1.0},
            "duration",
        ),
        ({"max_peak_amplitude": 0.0}, "peak"),
        ({"max_peak_amplitude": "invalid"}, "peak"),
        ({"max_clipping_ratio": float("nan")}, "clipping"),
    ],
)
def test_mastering_profile_rejects_invalid_public_settings(kwargs, message):
    with pytest.raises(MasteringError, match=message):
        MasteringProfile(**kwargs)


@pytest.mark.parametrize(
    "args, message",
    [
        ((-1, "segment-1", SEGMENT_WAV), "position"),
        ((1, "", SEGMENT_WAV), "ID"),
        ((1, "segment-1", b""), "audio"),
    ],
)
def test_mastering_segment_rejects_invalid_public_values(args, message):
    with pytest.raises(MasteringError, match=message):
        MasteringSegment(*args)


@pytest.mark.parametrize(
    "args, message",
    [
        (("", "v1", (segment(),), PROFILE), "episode ID"),
        (("episode-1", "", (segment(),), PROFILE), "episode version"),
        (("episode-1", "v1", (), PROFILE), "at least one segment"),
        (("episode-1", "v1", [segment()], PROFILE), "at least one segment"),
        (("episode-1", "v1", (segment(),), object()), "profile"),
    ],
)
def test_mastering_request_rejects_invalid_public_values(args, message):
    with pytest.raises(MasteringError, match=message):
        MasteringRequest(*args)


def test_mastering_rejects_duplicate_segment_positions_before_runner_dispatch():
    runner = FakeRunner(
        FfmpegResult(
            wav_bytes=SEGMENT_WAV,
            mp3_bytes=MASTERED_MP3,
            executable="fake-ffmpeg",
            version="fake-1.0",
            command=("fake-ffmpeg", "-i", "input.wav"),
            filters=("volume=1.0",),
            commands=(("fake-ffmpeg", "-i", "input.wav"),),
            mp3_metadata={
                "codec_name": "mp3",
                "sample_rate": "44100",
                "channels": "1",
                "duration": "0.02",
            },
        )
    )
    duplicate_position = MasteringSegment(1, "segment-duplicate", SEGMENT_WAV)

    with pytest.raises(MasteringError, match="positions must be unique"):
        MasteringService(runner).master(request(segment(), duplicate_position))

    assert runner.calls == []


@pytest.mark.parametrize(
    "profile, output_wav",
    [
        (
            MasteringProfile(max_duration_seconds=10.0, max_peak_amplitude=0.9),
            wav_bytes(samples=(32_000,) * 882),
        ),
        (
            MasteringProfile(max_duration_seconds=10.0, max_peak_amplitude=1.0),
            wav_bytes(samples=(32_767,) * 882),
        ),
    ],
    ids=["peak", "clipping"],
)
def test_mastering_rejects_output_that_fails_peak_or_clipping_gates(
    profile, output_wav
):
    runner = FakeRunner(
        FfmpegResult(
            wav_bytes=output_wav,
            mp3_bytes=MASTERED_MP3,
            executable="fake-ffmpeg",
            version="fake-1.0",
            command=("fake-ffmpeg", "-i", "input.wav"),
            filters=("volume=1.0",),
            commands=(("fake-ffmpeg", "-i", "input.wav"),),
            mp3_metadata={
                "codec_name": "mp3",
                "sample_rate": "44100",
                "channels": "1",
                "duration": "0.02",
            },
        )
    )

    with pytest.raises(MasteringError, match="peak or clipping"):
        MasteringService(runner).master(request(segment(), profile=profile))


@pytest.mark.parametrize(
    "audio_bytes, profile, message",
    [
        (SEGMENT_WAV[:-1], PROFILE, "media inspection"),
        (wav_bytes(sample_rate=22_050), PROFILE, "media inspection"),
        (wav_bytes(channels=2), PROFILE, "media inspection"),
        (wav_bytes(samples=(0,) * 882, sample_width=1), PROFILE, "mono 16-bit"),
        (wav_bytes(samples=(32_767,) * 882), PROFILE, "peak or clipping"),
        (wav_bytes(samples=(1_000,) * 10), PROFILE, "media inspection"),
        (
            wav_bytes(samples=(1_000,) * 1_000),
            MasteringProfile(
                max_duration_seconds=0.02,
                max_segment_duration_seconds=0.02,
            ),
            "media inspection",
        ),
    ],
    ids=[
        "truncated",
        "wrong-rate",
        "wrong-channels",
        "wrong-sample-width",
        "clipped",
        "too-short",
        "too-long",
    ],
)
def test_mastering_rejects_invalid_input_before_runner_dispatch(
    audio_bytes, profile, message
):
    runner = FakeRunner(
        FfmpegResult(
            wav_bytes=SEGMENT_WAV,
            mp3_bytes=MASTERED_MP3,
            executable="fake-ffmpeg",
            version="fake-1.0",
            command=("fake-ffmpeg", "-i", "input.wav"),
            filters=("volume=1.0",),
            commands=(("fake-ffmpeg", "-i", "input.wav"),),
            mp3_metadata={
                "codec_name": "mp3",
                "sample_rate": "44100",
                "channels": "1",
                "duration": "0.02",
            },
        )
    )

    with pytest.raises(MasteringError, match=message):
        MasteringService(runner).master(
            request(MasteringSegment(1, "invalid", audio_bytes), profile=profile)
        )

    assert runner.calls == []


def test_mastering_uses_segment_duration_bounds_before_output_duration_bounds():
    short_segment = wav_bytes(samples=(1_000,) * 882)
    completed_master = wav_bytes(samples=(1_000,) * 4_410)
    profile = MasteringProfile(
        min_duration_seconds=0.1,
        max_duration_seconds=1.0,
        min_segment_duration_seconds=0.01,
        max_segment_duration_seconds=0.05,
    )
    runner = FakeRunner(
        FfmpegResult(
            wav_bytes=completed_master,
            mp3_bytes=MASTERED_MP3,
            executable="fake-ffmpeg",
            version="fake-1.0",
            command=("fake-ffmpeg", "-i", "input.wav"),
            filters=("volume=1.0",),
            commands=(("fake-ffmpeg", "-i", "input.wav"),),
            mp3_metadata={
                "codec_name": "mp3",
                "sample_rate": "44100",
                "channels": "1",
                "duration": "0.1",
            },
        )
    )

    mastered = MasteringService(runner).master(
        request(segment(audio_bytes=short_segment), profile=profile)
    )

    assert mastered.diagnostics.duration_seconds == 0.1
    assert runner.calls == [(segment(audio_bytes=short_segment),)]


def test_mastering_rejects_missing_mp3_metadata():
    runner = FakeRunner(
        FfmpegResult(
            wav_bytes=SEGMENT_WAV,
            mp3_bytes=MASTERED_MP3,
            executable="fake-ffmpeg",
            version="fake-1.0",
            command=("fake-ffmpeg", "-i", "input.wav"),
            filters=("volume=1.0",),
            commands=(("fake-ffmpeg", "-i", "input.wav"),),
        )
    )

    with pytest.raises(MasteringError, match="MP3 metadata"):
        MasteringService(runner).master(request(segment()))


def test_mastering_rejects_malformed_provenance_commands():
    runner = FakeRunner(
        FfmpegResult(
            wav_bytes=SEGMENT_WAV,
            mp3_bytes=MASTERED_MP3,
            executable="fake-ffmpeg",
            version="fake-1.0",
            command=("fake-ffmpeg", "-i", "input.wav"),
            filters=("volume=1.0",),
            commands=(("fake-ffmpeg", "-i", "input.wav"), ("",)),
            mp3_metadata={
                "codec_name": "mp3",
                "sample_rate": "44100",
                "channels": "1",
                "duration": "0.02",
            },
        )
    )

    with pytest.raises(MasteringError, match="commands are malformed"):
        MasteringService(runner).master(request(segment()))

    assert runner.calls == [request(segment()).segments]


def test_mastering_normalizes_fake_runner_command_and_provenance():
    result = FfmpegResult(
        wav_bytes=SEGMENT_WAV,
        mp3_bytes=MASTERED_MP3,
        executable="fake-ffmpeg",
        version="fake-1.0",
        command=("fake-ffmpeg", "-i", "input.wav", "episode.wav"),
        filters=("aresample=44100", "pan=mono|c0=c0"),
        commands=(
            ("fake-ffmpeg", "-i", "input.wav", "episode.wav"),
            ("fake-ffmpeg", "-i", "input.wav", "episode.mp3"),
        ),
        mp3_metadata={
            "codec_name": "mp3",
            "sample_rate": "44100",
            "channels": "1",
            "duration": "0.02",
        },
    )
    runner = FakeRunner(result)

    mastered = MasteringService(runner).master(request(segment(20), segment(10)))

    assert [item.position for item in runner.calls[0]] == [10, 20]
    assert mastered.provenance.command == result.command
    assert mastered.provenance.commands == result.commands
    assert mastered.provenance.filters == result.filters
    assert mastered.provenance.mp3_metadata == result.mp3_metadata
    assert mastered.provenance.mp3_metadata is not result.mp3_metadata
    with pytest.raises(TypeError):
        mastered.provenance.mp3_metadata["codec_name"] = "wav"


def test_subprocess_mp3_inspector_rejects_ambiguous_streams(monkeypatch, tmp_path):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=b'{"streams": [{}, {}]}',
            stderr=b"",
        )

    monkeypatch.setattr(
        "poddown.audio.mastering.shutil.which", lambda executable: "fake-ffprobe"
    )
    monkeypatch.setattr("poddown.audio.mastering.subprocess.run", fake_run)

    with pytest.raises(MasteringError, match="ambiguous MP3 streams"):
        SubprocessMp3Inspector().inspect(tmp_path / "episode.mp3")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_subprocess_runner_surfaces_injected_mp3_inspection_failure():
    class FailingInspector:
        def inspect(self, path):
            raise MasteringError("ffprobe test failure")

    with pytest.raises(MasteringError, match="ffprobe test failure"):
        MasteringService(SubprocessFfmpegRunner(inspector=FailingInspector())).master(
            request(segment())
        )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_subprocess_ffmpeg_runner_masters_short_wav_fixture_with_provenance():
    mastered = MasteringService(SubprocessFfmpegRunner()).master(request(segment()))

    assert mastered.wav_bytes
    assert mastered.mp3_bytes
    assert mastered.diagnostics.sample_rate_hz == 44_100
    assert mastered.diagnostics.channels == 1
    assert mastered.diagnostics.duration_seconds >= PROFILE.min_duration_seconds
    assert Path(mastered.provenance.ffmpeg_executable).name.startswith("ffmpeg")
    assert mastered.provenance.ffmpeg_version.startswith("ffmpeg version")
    assert len(mastered.provenance.commands) == 2
    assert all(
        command[0] == mastered.provenance.ffmpeg_executable
        for command in mastered.provenance.commands
    )
