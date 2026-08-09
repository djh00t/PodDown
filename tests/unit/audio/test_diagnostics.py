"""Contract tests for deterministic decoded-WAV diagnostics."""

from io import BytesIO
import json
import wave

import pytest

from poddown.audio.diagnostics import AudioDiagnosticsError, diagnose_wav


def wav_bytes(
    *, sample_rate: int = 8_000, channels: int = 1, frames: tuple[int, ...] = (0, 1_000, -1_000, 0)
) -> bytes:
    """Build a small PCM fixture with independently checked sample values."""
    payload = b"".join(value.to_bytes(2, "little", signed=True) for value in frames)
    output = BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(payload * channels)
    return output.getvalue()


def test_valid_wav_reports_stable_objective_metrics_and_serialization():
    diagnostics = diagnose_wav(wav_bytes(), expected_sample_rate_hz=8_000)

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
