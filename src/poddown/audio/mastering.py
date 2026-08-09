"""Deterministic mastering and media-inspection boundaries."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
import wave
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, cast

from poddown.audio.diagnostics import (
    AudioDiagnostics,
    AudioDiagnosticsError,
    diagnose_wav,
)


class MasteringError(ValueError):
    """Raised when mastering cannot produce a verified result."""


@dataclass(frozen=True)
class MasteringProfile:
    """Versioned deterministic settings for one spoken-word master."""

    sample_rate_hz: int = 44_100
    channels: int = 1
    silence_ms: int = 100
    crossfade_ms: int = 0
    min_duration_seconds: float = 0.01
    max_duration_seconds: float = 10_800.0
    max_peak_amplitude: float = 0.99
    max_clipping_ratio: float = 0.0
    version: str = "spoken-word-v1"

    def __post_init__(self) -> None:
        if type(self.sample_rate_hz) is not int or self.sample_rate_hz <= 0:
            raise MasteringError("sample rate must be a positive integer")
        if type(self.channels) is not int or self.channels != 1:
            raise MasteringError("mastering currently requires mono output")
        for name, value in (
            ("silence_ms", self.silence_ms),
            ("crossfade_ms", self.crossfade_ms),
        ):
            if type(value) is not int or value < 0:
                raise MasteringError(f"{name} must be a non-negative integer")
        if (
            not isinstance(self.min_duration_seconds, (int, float))
            or isinstance(self.min_duration_seconds, bool)
            or self.min_duration_seconds <= 0
            or not math.isfinite(self.min_duration_seconds)
            or not isinstance(self.max_duration_seconds, (int, float))
            or isinstance(self.max_duration_seconds, bool)
            or self.max_duration_seconds < self.min_duration_seconds
            or not math.isfinite(self.max_duration_seconds)
        ):
            raise MasteringError("master duration bounds are invalid")
        if (
            not isinstance(self.max_peak_amplitude, (int, float))
            or isinstance(self.max_peak_amplitude, bool)
            or not math.isfinite(self.max_peak_amplitude)
            or not 0 < self.max_peak_amplitude <= 1
        ):
            raise MasteringError("maximum peak amplitude must be between zero and one")
        if (
            not isinstance(self.max_clipping_ratio, (int, float))
            or isinstance(self.max_clipping_ratio, bool)
            or not math.isfinite(self.max_clipping_ratio)
            or not 0 <= self.max_clipping_ratio <= 1
        ):
            raise MasteringError("maximum clipping ratio must be between zero and one")
        if not isinstance(self.version, str) or not self.version:
            raise MasteringError("mastering profile version must be non-empty")


@dataclass(frozen=True)
class MasteringSegment:
    """One accepted PCM WAV segment with a canonical position."""

    position: int
    segment_id: str
    audio_bytes: bytes

    def __post_init__(self) -> None:
        if type(self.position) is not int or self.position < 0:
            raise MasteringError("segment position must be a non-negative integer")
        if not isinstance(self.segment_id, str) or not self.segment_id:
            raise MasteringError("segment ID must be non-empty")
        if not isinstance(self.audio_bytes, bytes) or not self.audio_bytes:
            raise MasteringError("segment audio must be non-empty bytes")


@dataclass(frozen=True)
class MasteringRequest:
    """Immutable input snapshot for deterministic episode mastering."""

    episode_id: str
    episode_version: str
    segments: tuple[MasteringSegment, ...]
    profile: MasteringProfile

    def __post_init__(self) -> None:
        for name, value in (
            ("episode ID", self.episode_id),
            ("episode version", self.episode_version),
        ):
            if not isinstance(value, str) or not value:
                raise MasteringError(f"{name} must be non-empty")
        if not isinstance(self.segments, tuple) or not self.segments:
            raise MasteringError("mastering requires at least one segment")
        if not all(isinstance(segment, MasteringSegment) for segment in self.segments):
            raise MasteringError("segments must contain MasteringSegment values")
        if not isinstance(self.profile, MasteringProfile):
            raise MasteringError("profile must be MasteringProfile")


@dataclass(frozen=True)
class FfmpegResult:
    """Normalized output of one deterministic ffmpeg mastering invocation."""

    wav_bytes: bytes
    mp3_bytes: bytes
    executable: str
    version: str
    command: tuple[str, ...]
    filters: tuple[str, ...]
    commands: tuple[tuple[str, ...], ...] = ()
    mp3_metadata: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if self.mp3_metadata is not None:
            if not isinstance(self.mp3_metadata, Mapping):
                raise MasteringError("ffmpeg MP3 metadata must be a mapping")
            object.__setattr__(
                self,
                "mp3_metadata",
                MappingProxyType(dict(self.mp3_metadata)),
            )


class FfmpegRunner(Protocol):
    """Injectable ffmpeg boundary used by the mastering service."""

    def run(
        self,
        *,
        segments: tuple[MasteringSegment, ...],
        profile: MasteringProfile,
    ) -> FfmpegResult:
        """Master ordered segments into deterministic WAV and MP3 bytes."""


class Mp3Inspector(Protocol):
    """Inspect one encoded MP3 through an injectable media boundary."""

    def inspect(self, path: Path) -> dict[str, object]:
        """Return validated ffprobe stream metadata for the MP3 path."""


class SubprocessMp3Inspector:
    """Inspect MP3 output with a bounded local ffprobe subprocess."""

    def __init__(self, executable: str = "ffprobe") -> None:
        self.executable = shutil.which(executable) or executable

    def inspect(self, path: Path) -> dict[str, object]:
        """Run ffprobe without exposing process output or accepting ambiguity."""
        try:
            completed = subprocess.run(
                (
                    self.executable,
                    "-v",
                    "error",
                    "-show_entries",
                    "stream=codec_name,sample_rate,channels,duration",
                    "-of",
                    "json",
                    str(path),
                ),
                check=False,
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise MasteringError("ffprobe media inspection failed") from error
        if completed.returncode != 0:
            raise MasteringError("ffprobe rejected mastered MP3")
        try:
            payload = json.loads(completed.stdout)
            streams = payload["streams"]
            if (
                not isinstance(streams, list)
                or len(streams) != 1
                or not isinstance(streams[0], dict)
            ):
                raise MasteringError("ffprobe returned ambiguous MP3 streams")
            return cast(dict[str, object], streams[0])
        except MasteringError:
            raise
        except (
            IndexError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            raise MasteringError("ffprobe returned malformed MP3 metadata") from error


@dataclass(frozen=True)
class MasteringProvenance:
    """Reproducibility and media evidence for a mastered episode."""

    profile_version: str
    ffmpeg_executable: str
    ffmpeg_version: str
    command: tuple[str, ...]
    filters: tuple[str, ...]
    input_checksums: tuple[str, ...]
    wav_checksum: str
    mp3_checksum: str
    wav_metadata: Mapping[str, object]
    mp3_metadata: Mapping[str, object]
    commands: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self) -> None:
        for name in ("wav_metadata", "mp3_metadata"):
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise MasteringError(f"mastering provenance {name} is malformed")
            object.__setattr__(self, name, MappingProxyType(dict(value)))


@dataclass(frozen=True)
class MasteredAudio:
    """Verified deterministic master outputs and their provenance."""

    episode_id: str
    episode_version: str
    wav_bytes: bytes
    mp3_bytes: bytes
    diagnostics: AudioDiagnostics
    provenance: MasteringProvenance


class SubprocessFfmpegRunner:
    """Run a pinned local ffmpeg executable without exposing process output."""

    def __init__(
        self, executable: str = "ffmpeg", inspector: Mp3Inspector | None = None
    ) -> None:
        self.executable = shutil.which(executable) or executable
        self._inspector = inspector or SubprocessMp3Inspector()

    def run(
        self,
        *,
        segments: tuple[MasteringSegment, ...],
        profile: MasteringProfile,
    ) -> FfmpegResult:
        """Assemble and encode ordered segments through two bitexact commands."""
        assembled = _assemble_wav(segments, profile)
        filters = _filters(profile)
        wav_command = (
            self.executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-fflags",
            "+bitexact",
            "-i",
            "input.wav",
            "-map_metadata",
            "-1",
            "-af",
            ",".join(filters),
            "-ar",
            str(profile.sample_rate_hz),
            "-ac",
            str(profile.channels),
            "-c:a",
            "pcm_s16le",
            "episode.wav",
        )
        mp3_command = (
            self.executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-fflags",
            "+bitexact",
            "-i",
            "input.wav",
            "-map_metadata",
            "-1",
            "-af",
            ",".join(filters),
            "-ar",
            str(profile.sample_rate_hz),
            "-ac",
            str(profile.channels),
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            "-write_xing",
            "0",
            "episode.mp3",
        )
        with tempfile.TemporaryDirectory(prefix="poddown-master-") as directory:
            root = Path(directory)
            (root / "input.wav").write_bytes(assembled)
            self._invoke(wav_command, root)
            self._invoke(mp3_command, root)
            wav_bytes = (root / "episode.wav").read_bytes()
            mp3_bytes = (root / "episode.mp3").read_bytes()
            mp3_metadata = self._inspector.inspect(root / "episode.mp3")
        version = self._version()
        return FfmpegResult(
            wav_bytes=wav_bytes,
            mp3_bytes=mp3_bytes,
            executable=self.executable,
            version=version,
            command=wav_command,
            filters=filters,
            commands=(wav_command, mp3_command),
            mp3_metadata=mp3_metadata,
        )

    def _invoke(self, command: tuple[str, ...], directory: Path) -> None:
        try:
            completed = subprocess.run(
                command,
                cwd=directory,
                check=False,
                capture_output=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise MasteringError("ffmpeg mastering process failed") from error
        if completed.returncode != 0:
            raise MasteringError("ffmpeg mastering process returned an error")

    def _version(self) -> str:
        try:
            completed = subprocess.run(
                (self.executable, "-version"),
                check=False,
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise MasteringError("ffmpeg version could not be read") from error
        if completed.returncode != 0:
            raise MasteringError("ffmpeg version command failed")
        first_line = completed.stdout.decode("utf-8", errors="replace").splitlines()
        if not first_line:
            raise MasteringError("ffmpeg version output is empty")
        return first_line[0]


class MasteringService:
    """Validate accepted segments, invoke mastering, and verify both outputs."""

    def __init__(self, runner: FfmpegRunner | None = None) -> None:
        self._runner = runner or SubprocessFfmpegRunner()

    def master(self, request: MasteringRequest) -> MasteredAudio:
        """Return a verified deterministic master or fail closed."""
        if not isinstance(request, MasteringRequest):
            raise MasteringError("request must be MasteringRequest")
        segments = tuple(sorted(request.segments, key=lambda segment: segment.position))
        self._validate_positions(segments)
        for segment in segments:
            _diagnose_segment(segment.audio_bytes, request.profile)
        input_checksums = tuple(
            sha256(segment.audio_bytes).hexdigest() for segment in segments
        )
        try:
            result = self._runner.run(segments=segments, profile=request.profile)
            wav_bytes = result.wav_bytes
            mp3_bytes = result.mp3_bytes
            executable = result.executable
            version = result.version
            command = result.command
            filters = result.filters
            commands = getattr(result, "commands", ())
            mp3_metadata = getattr(result, "mp3_metadata", None)
        except MasteringError:
            raise
        except Exception as error:
            raise MasteringError("mastering runner failed") from error
        if not isinstance(wav_bytes, bytes) or not wav_bytes:
            raise MasteringError("mastering runner returned empty WAV bytes")
        if not isinstance(mp3_bytes, bytes) or not mp3_bytes:
            raise MasteringError("mastering runner returned empty MP3 bytes")
        try:
            diagnostics = diagnose_wav(
                wav_bytes,
                expected_sample_rate_hz=request.profile.sample_rate_hz,
                expected_channels=request.profile.channels,
                min_duration_seconds=request.profile.min_duration_seconds,
                max_duration_seconds=request.profile.max_duration_seconds,
            )
        except AudioDiagnosticsError as error:
            raise MasteringError("mastered WAV failed media inspection") from error
        if (
            diagnostics.peak_amplitude > request.profile.max_peak_amplitude
            or diagnostics.clipping_ratio > request.profile.max_clipping_ratio
        ):
            raise MasteringError("mastered WAV failed peak or clipping gates")
        if not isinstance(mp3_metadata, Mapping):
            raise MasteringError("mastering provenance MP3 metadata is missing")
        _validate_mp3_metadata(mp3_metadata, profile=request.profile)
        if not isinstance(executable, str) or not executable:
            raise MasteringError("mastering provenance lacks executable")
        if not isinstance(version, str) or not version:
            raise MasteringError("mastering provenance lacks ffmpeg version")
        if not isinstance(command, tuple) or not all(
            isinstance(item, str) and item for item in command
        ):
            raise MasteringError("mastering provenance command is malformed")
        if not isinstance(filters, tuple) or not all(
            isinstance(item, str) and item for item in filters
        ):
            raise MasteringError("mastering provenance filters are malformed")
        if (
            not isinstance(commands, tuple)
            or not commands
            or not all(
                isinstance(command, tuple)
                and command
                and all(isinstance(item, str) and item for item in command)
                for command in commands
            )
        ):
            raise MasteringError("mastering provenance commands are malformed")
        normalized_mp3_metadata = dict(mp3_metadata)
        provenance = MasteringProvenance(
            profile_version=request.profile.version,
            ffmpeg_executable=executable,
            ffmpeg_version=version,
            command=command,
            filters=filters,
            input_checksums=input_checksums,
            wav_checksum=sha256(wav_bytes).hexdigest(),
            mp3_checksum=sha256(mp3_bytes).hexdigest(),
            wav_metadata=cast(dict[str, object], diagnostics.to_dict()),
            mp3_metadata=normalized_mp3_metadata,
            commands=commands,
        )
        return MasteredAudio(
            episode_id=request.episode_id,
            episode_version=request.episode_version,
            wav_bytes=wav_bytes,
            mp3_bytes=mp3_bytes,
            diagnostics=diagnostics,
            provenance=provenance,
        )

    @staticmethod
    def _validate_positions(segments: tuple[MasteringSegment, ...]) -> None:
        positions = tuple(segment.position for segment in segments)
        if positions != tuple(sorted(set(positions))):
            raise MasteringError("segment positions must be unique")


def _diagnose_segment(
    audio_bytes: bytes, profile: MasteringProfile
) -> AudioDiagnostics:
    try:
        diagnostics = diagnose_wav(
            audio_bytes,
            expected_sample_rate_hz=profile.sample_rate_hz,
            expected_channels=profile.channels,
            min_duration_seconds=profile.min_duration_seconds,
            max_duration_seconds=profile.max_duration_seconds,
        )
    except AudioDiagnosticsError as error:
        raise MasteringError("input segment failed media inspection") from error
    if (
        diagnostics.peak_amplitude > profile.max_peak_amplitude
        or diagnostics.clipping_ratio > profile.max_clipping_ratio
    ):
        raise MasteringError("input segment failed peak or clipping gates")
    _pcm_payload(audio_bytes)
    return diagnostics


def _assemble_wav(
    segments: tuple[MasteringSegment, ...], profile: MasteringProfile
) -> bytes:
    ordered = tuple(sorted(segments, key=lambda segment: segment.position))
    payloads = tuple(_pcm_payload(segment.audio_bytes) for segment in ordered)
    silence_frames = round(profile.sample_rate_hz * profile.silence_ms / 1000)
    crossfade_frames = round(profile.sample_rate_hz * profile.crossfade_ms / 1000)
    combined = bytearray(payloads[0])
    frame_width = 2 * profile.channels
    for payload in payloads[1:]:
        if crossfade_frames:
            if (
                len(combined) < crossfade_frames * frame_width
                or len(payload) < crossfade_frames * frame_width
            ):
                raise MasteringError("crossfade exceeds segment duration")
            start = len(combined) - crossfade_frames * frame_width
            for index in range(crossfade_frames):
                previous = int.from_bytes(
                    combined[start + index * 2 : start + index * 2 + 2],
                    "little",
                    signed=True,
                )
                current = int.from_bytes(
                    payload[index * 2 : index * 2 + 2], "little", signed=True
                )
                weight = index / max(crossfade_frames - 1, 1)
                mixed = round(previous * (1 - weight) + current * weight)
                combined[start + index * 2 : start + index * 2 + 2] = mixed.to_bytes(
                    2, "little", signed=True
                )
            combined.extend(payload[crossfade_frames * frame_width :])
        else:
            combined.extend(b"\x00" * silence_frames * frame_width)
            combined.extend(payload)
    stream = BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(profile.channels)
        output.setsampwidth(2)
        output.setframerate(profile.sample_rate_hz)
        output.writeframes(bytes(combined))
    return stream.getvalue()


def _pcm_payload(audio_bytes: bytes) -> bytes:
    try:
        with wave.open(BytesIO(audio_bytes), "rb") as audio:
            if audio.getsampwidth() != 2 or audio.getnchannels() != 1:
                raise MasteringError("mastering requires mono 16-bit PCM WAV")
            frame_count = audio.getnframes()
            payload = audio.readframes(frame_count)
    except MasteringError:
        raise
    except (EOFError, OSError, ValueError, wave.Error) as error:
        raise MasteringError("mastering input WAV cannot be decoded") from error
    if len(payload) != frame_count * 2:
        raise MasteringError("mastering input WAV is truncated")
    return payload


def _filters(profile: MasteringProfile) -> tuple[str, ...]:
    return (
        f"aresample={profile.sample_rate_hz}",
        "pan=mono|c0=c0",
        "volume=1.0",
    )


def _validate_mp3_metadata(
    metadata: Mapping[str, object],
    *,
    profile: MasteringProfile,
) -> None:
    """Validate ffprobe metadata when the runner supplies it."""
    if metadata.get("codec_name") != "mp3":
        raise MasteringError("mastered MP3 codec does not match expectation")
    try:
        sample_rate = int(cast(str | int, metadata["sample_rate"]))
        channels = int(cast(str | int, metadata["channels"]))
        duration = float(cast(str | int | float, metadata["duration"]))
    except (KeyError, TypeError, ValueError) as error:
        raise MasteringError("mastered MP3 metadata is malformed") from error
    if sample_rate != profile.sample_rate_hz or channels != profile.channels:
        raise MasteringError("mastered MP3 format does not match profile")
    if not math.isfinite(duration) or not (
        profile.min_duration_seconds <= duration <= profile.max_duration_seconds
    ):
        raise MasteringError("mastered MP3 duration is outside profile bounds")


__all__ = [
    "FfmpegResult",
    "FfmpegRunner",
    "Mp3Inspector",
    "MasteredAudio",
    "MasteringError",
    "MasteringProfile",
    "MasteringProvenance",
    "MasteringRequest",
    "MasteringSegment",
    "MasteringService",
    "SubprocessMp3Inspector",
    "SubprocessFfmpegRunner",
]
