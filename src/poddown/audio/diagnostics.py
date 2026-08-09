"""Deterministic diagnostics for decoded PCM WAV audio."""

import wave
from dataclasses import dataclass
from io import BytesIO


class AudioDiagnosticsError(ValueError):
    """Raised when WAV audio cannot be safely diagnosed."""


@dataclass(frozen=True)
class AudioDiagnostics:
    """Objective, serializable measurements of one decoded WAV artifact."""

    sample_rate_hz: int
    channels: int
    duration_seconds: float
    peak_amplitude: float
    clipping_ratio: float
    silence_ratio: float

    @property
    def passes_hard_gates(self) -> bool:
        """Return whether objective audio metrics meet this slice's hard gates."""
        return (
            self.sample_rate_hz > 0
            and self.channels == 1
            and self.duration_seconds > 0
            and self.clipping_ratio == 0.0
        )

    def to_dict(self) -> dict[str, float | int]:
        """Return metrics in a stable, JSON-serializable field order."""
        return {
            "channels": self.channels,
            "clipping_ratio": self.clipping_ratio,
            "duration_seconds": self.duration_seconds,
            "peak_amplitude": self.peak_amplitude,
            "sample_rate_hz": self.sample_rate_hz,
            "silence_ratio": self.silence_ratio,
        }


def diagnose_wav(
    audio_bytes: bytes,
    *,
    expected_sample_rate_hz: int | None = None,
    expected_channels: int | None = None,
    min_duration_seconds: float | None = None,
    max_duration_seconds: float | None = None,
) -> AudioDiagnostics:
    """Decode PCM WAV bytes and enforce requested metadata and duration bounds."""
    _validate_bounds(
        expected_sample_rate_hz,
        expected_channels,
        min_duration_seconds,
        max_duration_seconds,
    )
    if not isinstance(audio_bytes, bytes) or not audio_bytes:
        raise AudioDiagnosticsError("invalid WAV audio bytes")

    try:
        with wave.open(BytesIO(audio_bytes), "rb") as audio:
            channels = audio.getnchannels()
            sample_width = audio.getsampwidth()
            sample_rate_hz = audio.getframerate()
            frame_count = audio.getnframes()
            compression = audio.getcomptype()
            if compression != "NONE":
                raise AudioDiagnosticsError("invalid WAV compression")
            if channels <= 0 or sample_rate_hz <= 0 or frame_count <= 0:
                raise AudioDiagnosticsError("invalid WAV metadata")
            if sample_width not in (1, 2, 3, 4):
                raise AudioDiagnosticsError("invalid WAV sample width")
            payload = audio.readframes(frame_count)
    except AudioDiagnosticsError:
        raise
    except (EOFError, OSError, ValueError, wave.Error) as error:
        raise AudioDiagnosticsError("malformed or invalid WAV audio") from error

    expected_size = frame_count * channels * sample_width
    if len(payload) != expected_size:
        raise AudioDiagnosticsError("truncated WAV frame payload")
    if (
        expected_sample_rate_hz is not None
        and sample_rate_hz != expected_sample_rate_hz
    ):
        raise AudioDiagnosticsError("WAV sample rate does not match expectation")
    if expected_channels is not None and channels != expected_channels:
        raise AudioDiagnosticsError("WAV channel count does not match expectation")

    duration_seconds = frame_count / sample_rate_hz
    if min_duration_seconds is not None and duration_seconds < min_duration_seconds:
        raise AudioDiagnosticsError("WAV duration is below the minimum")
    if max_duration_seconds is not None and duration_seconds > max_duration_seconds:
        raise AudioDiagnosticsError("WAV duration exceeds the maximum")

    samples = tuple(_decode_samples(payload, sample_width))
    maximum = (1 << (sample_width * 8 - 1)) - 1
    peak_amplitude = min(max(abs(sample) for sample in samples) / maximum, 1.0)
    clipping_ratio = sum(abs(sample) >= maximum for sample in samples) / len(samples)
    silence_ratio = sum(sample == 0 for sample in samples) / len(samples)
    return AudioDiagnostics(
        sample_rate_hz=sample_rate_hz,
        channels=channels,
        duration_seconds=duration_seconds,
        peak_amplitude=peak_amplitude,
        clipping_ratio=clipping_ratio,
        silence_ratio=silence_ratio,
    )


def _validate_bounds(
    expected_sample_rate_hz: int | None,
    expected_channels: int | None,
    min_duration_seconds: float | None,
    max_duration_seconds: float | None,
) -> None:
    if expected_sample_rate_hz is not None and (
        type(expected_sample_rate_hz) is not int or expected_sample_rate_hz <= 0
    ):
        raise AudioDiagnosticsError("expected sample rate must be a positive integer")
    if expected_channels is not None and (
        type(expected_channels) is not int or expected_channels <= 0
    ):
        raise AudioDiagnosticsError("expected channels must be a positive integer")
    for name, value in (
        ("minimum duration", min_duration_seconds),
        ("maximum duration", max_duration_seconds),
    ):
        if value is not None and (not isinstance(value, (int, float)) or value < 0):
            raise AudioDiagnosticsError(f"{name} must be a non-negative number")
    if (
        min_duration_seconds is not None
        and max_duration_seconds is not None
        and min_duration_seconds > max_duration_seconds
    ):
        raise AudioDiagnosticsError("minimum duration exceeds maximum duration")


def _decode_samples(payload: bytes, sample_width: int) -> tuple[int, ...]:
    if sample_width == 1:
        return tuple(sample - 128 for sample in payload)
    if sample_width == 2:
        return tuple(
            int.from_bytes(payload[index : index + 2], "little", signed=True)
            for index in range(0, len(payload), 2)
        )
    if sample_width == 3:
        return tuple(
            int.from_bytes(
                payload[index : index + 3]
                + (b"\xff" if payload[index + 2] & 0x80 else b"\x00"),
                "little",
                signed=True,
            )
            for index in range(0, len(payload), 3)
        )
    return tuple(
        int.from_bytes(payload[index : index + 4], "little", signed=True)
        for index in range(0, len(payload), 4)
    )
