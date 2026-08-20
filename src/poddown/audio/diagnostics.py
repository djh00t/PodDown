"""Deterministic diagnostics for decoded PCM WAV audio."""

import sys
import wave
from array import array
from collections.abc import Iterator
from dataclasses import dataclass
from io import BytesIO

_PCM_CHUNK_FRAMES = 8_192


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
            expected_size = frame_count * channels * sample_width
            if (
                expected_sample_rate_hz is not None
                and sample_rate_hz != expected_sample_rate_hz
            ):
                raise AudioDiagnosticsError(
                    "WAV sample rate does not match expectation"
                )
            if expected_channels is not None and channels != expected_channels:
                raise AudioDiagnosticsError(
                    "WAV channel count does not match expectation"
                )

            duration_seconds = frame_count / sample_rate_hz
            if (
                min_duration_seconds is not None
                and duration_seconds < min_duration_seconds
            ):
                raise AudioDiagnosticsError("WAV duration is below the minimum")
            if (
                max_duration_seconds is not None
                and duration_seconds > max_duration_seconds
            ):
                raise AudioDiagnosticsError("WAV duration exceeds the maximum")

            maximum = (1 << (sample_width * 8 - 1)) - 1
            peak = 0
            clipping_samples = 0
            silent_samples = 0
            remaining_frames = frame_count
            read_size = 0
            while remaining_frames:
                chunk_frames = min(remaining_frames, _PCM_CHUNK_FRAMES)
                payload = audio.readframes(chunk_frames)
                expected_chunk_size = chunk_frames * channels * sample_width
                if len(payload) != expected_chunk_size:
                    raise AudioDiagnosticsError("truncated WAV frame payload")
                read_size += len(payload)
                fixed_metrics = _fixed_width_metrics(payload, sample_width, maximum)
                if fixed_metrics is None:
                    for sample in _decode_samples(payload, sample_width):
                        peak = max(peak, abs(sample))
                        clipping_samples += abs(sample) >= maximum
                        silent_samples += sample == 0
                else:
                    chunk_peak, chunk_clipping, chunk_silence = fixed_metrics
                    peak = max(peak, chunk_peak)
                    clipping_samples += chunk_clipping
                    silent_samples += chunk_silence
                remaining_frames -= chunk_frames
    except AudioDiagnosticsError:
        raise
    except (EOFError, OSError, ValueError, wave.Error) as error:
        raise AudioDiagnosticsError("malformed or invalid WAV audio") from error

    if read_size != expected_size:
        raise AudioDiagnosticsError("truncated WAV frame payload")
    sample_count = frame_count * channels
    peak_amplitude = min(peak / maximum, 1.0)
    clipping_ratio = clipping_samples / sample_count
    silence_ratio = silent_samples / sample_count
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


def _decode_samples(payload: bytes, sample_width: int) -> Iterator[int]:
    if sample_width == 1:
        yield from (sample - 128 for sample in payload)
    elif sample_width in (2, 4):
        typecode = "h" if sample_width == 2 else "i"
        values = array(typecode)
        if values.itemsize != sample_width:
            raise AudioDiagnosticsError("unsupported native PCM sample width")
        try:
            values.frombytes(payload)
        except (OverflowError, ValueError) as error:
            raise AudioDiagnosticsError("invalid PCM sample payload") from error
        if sys.byteorder != "little":
            values.byteswap()
        yield from values
    elif sample_width == 3:
        for index in range(0, len(payload), 3):
            yield int.from_bytes(
                payload[index : index + 3]
                + (b"\xff" if payload[index + 2] & 0x80 else b"\x00"),
                "little",
                signed=True,
            )
    else:
        for index in range(0, len(payload), 4):
            yield int.from_bytes(payload[index : index + 4], "little", signed=True)


def _fixed_width_metrics(
    payload: bytes, sample_width: int, maximum: int
) -> tuple[int, int, int] | None:
    """Aggregate common PCM widths with array's native C-level counters."""
    if sample_width == 1:
        typecode = "B"
    elif sample_width == 2:
        typecode = "h"
    elif sample_width == 4:
        typecode = "i"
    else:
        return None
    values = array(typecode)
    if values.itemsize != sample_width:
        raise AudioDiagnosticsError("unsupported native PCM sample width")
    try:
        values.frombytes(payload)
    except (OverflowError, ValueError) as error:
        raise AudioDiagnosticsError("invalid PCM sample payload") from error
    if sys.byteorder != "little" and sample_width != 1:
        values.byteswap()
    if sample_width == 1:
        high = max(values) - 128
        low = min(values) - 128
        return (
            max(abs(high), abs(low)),
            values.count(1) + values.count(255),
            values.count(128),
        )
    high = max(values)
    low = min(values)
    return (
        max(abs(high), abs(low)),
        values.count(maximum) + values.count(-maximum - 1),
        values.count(0),
    )
