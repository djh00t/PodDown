"""Contract tests for deterministic decoded-WAV diagnostics."""

import json
import wave
from io import BytesIO
from unittest.mock import patch

import pytest

from poddown.audio.diagnostics import (
    AudioDiagnostics,
    AudioDiagnosticsError,
    diagnose_wav,
)


def wav_bytes(
    *,
    sample_rate: int = 8_000,
    channels: int = 1,
    sample_width: int = 2,
    frames: tuple[int, ...] = (0, 1_000, -1_000, 0),
) -> bytes:
    """Build a small PCM fixture with independently checked sample values."""
    if sample_width == 1:
        payload = bytes(value + 128 for value in frames)
    else:
        payload = b"".join(
            value.to_bytes(sample_width, "little", signed=True) for value in frames
        )
    output = BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(sample_width)
        stream.setframerate(sample_rate)
        stream.writeframes(payload * channels)
    return output.getvalue()


def test_valid_wav_reports_stable_objective_metrics_and_serialization():
    diagnostics = diagnose_wav(
        wav_bytes(), expected_sample_rate_hz=8_000, expected_channels=1
    )

    assert diagnostics.sample_rate_hz == 8_000
    assert diagnostics.channels == 1
    assert diagnostics.duration_seconds == 4 / 8_000
    assert diagnostics.peak_amplitude == pytest.approx(1_000 / 32_767)
    assert diagnostics.clipping_ratio == 0.0
    assert diagnostics.silence_ratio == 0.5
    assert diagnostics.to_dict() == {
        "channels": 1,
        "clipping_ratio": 0.0,
        "duration_seconds": 0.0005,
        "peak_amplitude": pytest.approx(1_000 / 32_767),
        "sample_rate_hz": 8_000,
        "silence_ratio": 0.5,
    }
    assert json.dumps(diagnostics.to_dict(), sort_keys=True) == json.dumps(
        diagnostics.to_dict(), sort_keys=True
    )


@pytest.mark.parametrize("audio", [b"not a wav", wav_bytes()[:-1]])
def test_malformed_or_truncated_wav_is_rejected_before_metrics(audio: bytes):
    with pytest.raises(AudioDiagnosticsError, match="(malformed|truncated|invalid)"):
        diagnose_wav(audio, expected_sample_rate_hz=8_000)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"expected_sample_rate_hz": 16_000}, "sample rate"),
        ({"min_duration_seconds": 0.001}, "duration"),
        ({"max_duration_seconds": 0.0001}, "duration"),
    ],
)
def test_wav_metadata_and_duration_bounds_are_hard_diagnostic_failures(kwargs, message):
    with pytest.raises(AudioDiagnosticsError, match=message):
        diagnose_wav(wav_bytes(), **kwargs)


def test_clipping_and_silence_metrics_are_deterministic():
    diagnostics = diagnose_wav(
        wav_bytes(frames=(32_767, -32_768, 0, 0)), expected_sample_rate_hz=8_000
    )

    assert diagnostics.clipping_ratio == 0.5
    assert diagnostics.silence_ratio == 0.5


def test_wav_metrics_are_aggregated_in_bounded_pcm_chunks():
    reader = _ChunkedWave((0, 1_000, -1_000, 32_767, -32_768, 0) * 4_000)

    with patch("poddown.audio.diagnostics.wave.open", return_value=reader):
        diagnostics = diagnose_wav(b"fixture")

    assert diagnostics.peak_amplitude == 1.0
    assert diagnostics.clipping_ratio == pytest.approx(2 / 6)
    assert diagnostics.silence_ratio == pytest.approx(2 / 6)
    assert max(reader.read_sizes) < reader.getnframes()


@pytest.mark.parametrize("sample_width", [1, 3, 4])
def test_supported_pcm_sample_widths_decode_deterministically(sample_width: int):
    diagnostics = diagnose_wav(
        wav_bytes(sample_width=sample_width, frames=(0, 1, -1, 0))
    )

    assert diagnostics.duration_seconds == 4 / 8_000
    assert diagnostics.silence_ratio == 0.5


@pytest.mark.parametrize(
    ("audio", "message"),
    [(b"", "invalid WAV audio bytes"), (None, "invalid WAV audio bytes")],
)
def test_empty_or_non_bytes_input_fails_closed(audio: bytes | None, message: str):
    with pytest.raises(AudioDiagnosticsError, match=message):
        diagnose_wav(audio)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"expected_sample_rate_hz": 0},
        {"expected_channels": 0},
        {"min_duration_seconds": -1},
        {"max_duration_seconds": "1"},
        {"min_duration_seconds": 2, "max_duration_seconds": 1},
    ],
)
def test_invalid_diagnostic_bounds_are_rejected(kwargs: dict[str, object]):
    with pytest.raises(AudioDiagnosticsError):
        diagnose_wav(wav_bytes(), **kwargs)  # type: ignore[arg-type]


def test_channel_expectation_is_a_hard_diagnostic_gate():
    with pytest.raises(AudioDiagnosticsError, match="channel"):
        diagnose_wav(wav_bytes(channels=2), expected_channels=1)


@pytest.mark.parametrize(
    "diagnostics",
    [
        AudioDiagnostics(0, 1, 1.0, 0.5, 0.0, 0.1),
        AudioDiagnostics(44_100, 2, 1.0, 0.5, 0.0, 0.1),
        AudioDiagnostics(44_100, 1, 0.0, 0.5, 0.0, 0.1),
        AudioDiagnostics(44_100, 1, 1.0, 0.5, 1.0, 0.1),
    ],
)
def test_invalid_audio_metrics_fail_hard_gates(diagnostics: AudioDiagnostics):
    assert diagnostics.passes_hard_gates is False


class _FakeWave:
    """Minimal wave reader fixture for metadata failure branches."""

    def __init__(self, *, channels: int, sample_width: int, compression: str):
        self.channels = channels
        self.sample_width = sample_width
        self.compression = compression

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def getnchannels(self):
        return self.channels

    def getsampwidth(self):
        return self.sample_width

    def getframerate(self):
        return 8_000

    def getnframes(self):
        return 1

    def getcomptype(self):
        return self.compression

    def readframes(self, _frame_count):
        return b"\0\0"


class _ChunkedWave(_FakeWave):
    """PCM reader that rejects one-shot reads of its full payload."""

    def __init__(self, samples: tuple[int, ...]):
        super().__init__(channels=1, sample_width=2, compression="NONE")
        self.samples = samples
        self.read_sizes: list[int] = []
        self.position = 0

    def getnframes(self):
        return len(self.samples)

    def readframes(self, frame_count):
        self.read_sizes.append(frame_count)
        if frame_count >= self.getnframes():
            raise AssertionError("diagnostics must read PCM in bounded chunks")
        end = min(self.position + frame_count, self.getnframes())
        payload = b"".join(
            sample.to_bytes(2, "little", signed=True)
            for sample in self.samples[self.position : end]
        )
        self.position = end
        return payload


@pytest.mark.parametrize(
    ("reader", "message"),
    [
        (_FakeWave(channels=1, sample_width=2, compression="ULAW"), "compression"),
        (_FakeWave(channels=0, sample_width=2, compression="NONE"), "metadata"),
        (_FakeWave(channels=1, sample_width=5, compression="NONE"), "sample width"),
    ],
)
def test_invalid_wave_metadata_is_rejected(reader: _FakeWave, message: str):
    with (
        patch("poddown.audio.diagnostics.wave.open", return_value=reader),
        pytest.raises(AudioDiagnosticsError, match=message),
    ):
        diagnose_wav(b"fixture")
