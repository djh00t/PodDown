"""Deterministic assembly of the immutable episode-package payloads."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from types import MappingProxyType
from uuid import UUID

from poddown.audio.mastering import MasteredAudio, MasteringProvenance
from poddown.content.models import Profile, ScriptVersion, SourceSnapshot
from poddown.content.segmentation import Segment
from poddown.packages import (
    REQUIRED_PACKAGE_ARTIFACTS,
    PackageArtifact,
    PackageProvenance,
)
from poddown.qa.final_master import FinalMasterQaResult


class PackageGenerationError(ValueError):
    """Raised when immutable package evidence is incomplete or inconsistent."""


_MEDIA_TYPES = {
    "episode.wav": "audio/wav",
    "episode.mp3": "audio/mpeg",
    "transcript.txt": "text/plain; charset=utf-8",
    "transcript.vtt": "text/vtt; charset=utf-8",
    "chapters.json": "application/json",
    "show-notes.md": "text/markdown; charset=utf-8",
    "qa-report.json": "application/json",
    "provenance.json": "application/json",
    "render-manifest.json": "application/json",
}


@dataclass(frozen=True)
class PackageGenerationInput:
    """All immutable evidence needed to generate an episode package."""

    episode_version_id: UUID
    source: SourceSnapshot
    profile: Profile
    script: ScriptVersion
    script_version: int
    segments: tuple[Segment, ...]
    content_manifest: Mapping[str, object]
    mastered_audio: MasteredAudio
    final_qa: FinalMasterQaResult
    render_evidence: Mapping[str, object]
    renderer: str
    lexicon_evidence: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.episode_version_id, UUID):
            raise PackageGenerationError("episode version ID must be a UUID")
        if not isinstance(self.source, SourceSnapshot):
            raise PackageGenerationError("source must be a SourceSnapshot")
        if not isinstance(self.profile, Profile):
            raise PackageGenerationError("profile must be a Profile")
        if not isinstance(self.script, ScriptVersion):
            raise PackageGenerationError("script must be a ScriptVersion")
        if type(self.script_version) is not int or self.script_version <= 0:
            raise PackageGenerationError("script version must be a positive integer")
        if not isinstance(self.segments, tuple) or not self.segments:
            raise PackageGenerationError("segments must be a non-empty tuple")
        if not all(isinstance(segment, Segment) for segment in self.segments):
            raise PackageGenerationError("segments must contain Segment values")
        segment_ids = tuple(segment.segment_id for segment in self.segments)
        if len(set(segment_ids)) != len(segment_ids):
            raise PackageGenerationError("segment IDs must be unique")
        if not isinstance(self.mastered_audio, MasteredAudio):
            raise PackageGenerationError("mastered audio is malformed")
        if not isinstance(self.final_qa, FinalMasterQaResult):
            raise PackageGenerationError("final-master QA result is malformed")
        if not isinstance(self.renderer, str) or not self.renderer:
            raise PackageGenerationError("renderer must be non-empty")
        for name in ("content_manifest", "render_evidence", "lexicon_evidence"):
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise PackageGenerationError(f"{name} must be a mapping")
            object.__setattr__(self, name, MappingProxyType(dict(value)))


def _json_value(value: object) -> object:
    """Convert accepted JSON-native evidence into deterministic built-ins."""
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise PackageGenerationError("evidence contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise PackageGenerationError("evidence mapping keys must be strings")
            result[key] = _json_value(item)
        return result
    if isinstance(value, tuple | list):
        return [_json_value(item) for item in value]
    raise PackageGenerationError("evidence must be JSON-native")


def _json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            _json_value(value), allow_nan=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as error:
        raise PackageGenerationError("evidence is not JSON serializable") from error


def _validate_request(request: PackageGenerationInput) -> None:
    if not isinstance(request, PackageGenerationInput):
        raise PackageGenerationError("package generation input is malformed")
    if request.script.source_sha256 != request.source.source_sha256:
        raise PackageGenerationError("script is not bound to its source")
    if request.script.profile_id != request.profile.profile_id:
        raise PackageGenerationError("script is not bound to its profile")
    canonical_hash = request.content_manifest.get("script")
    if not isinstance(canonical_hash, Mapping) or (
        canonical_hash.get("canonical_hash") != request.script.canonical_hash
    ):
        raise PackageGenerationError("content manifest is not bound to its script")
    workflow_id = request.render_evidence.get("workflow_id")
    if not isinstance(workflow_id, str) or not workflow_id.strip():
        raise PackageGenerationError("render evidence must contain a workflow ID")

    master = request.mastered_audio
    provenance = master.provenance
    if not isinstance(provenance, MasteringProvenance):
        raise PackageGenerationError("mastering provenance is malformed")
    if not isinstance(master.wav_bytes, bytes) or not isinstance(
        master.mp3_bytes, bytes
    ):
        raise PackageGenerationError("master audio must contain byte payloads")
    wav_checksum = sha256(master.wav_bytes).hexdigest()
    mp3_checksum = sha256(master.mp3_bytes).hexdigest()
    if (
        provenance.wav_checksum != wav_checksum
        or provenance.mp3_checksum != mp3_checksum
    ):
        raise PackageGenerationError("master audio checksums are not bound")
    if request.final_qa.master_checksum != wav_checksum:
        raise PackageGenerationError("final QA is not bound to the WAV master")
    if request.final_qa.subject_id != master.episode_id:
        raise PackageGenerationError("final QA is not bound to the mastered episode")
    if request.final_qa.transcript.checksum != wav_checksum:
        raise PackageGenerationError("transcript is not bound to the WAV master")
    if not request.final_qa.passed:
        raise PackageGenerationError("final-master QA did not pass")
    if request.final_qa.critical_token_accuracy != 1.0:
        raise PackageGenerationError("critical-token accuracy must be exactly 1.0")

    _json_bytes(request.content_manifest)
    _json_bytes(request.render_evidence)
    _json_bytes(request.lexicon_evidence)


def _vtt_timestamp(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"


def _transcript_vtt(request: PackageGenerationInput) -> bytes:
    words = request.final_qa.transcript.words
    if not words:
        raise PackageGenerationError("timestamped transcript words are required")
    previous_end = 0
    cues: list[str] = []
    for word in words:
        if (
            not isinstance(word.word, str)
            or not word.word.strip()
            or not isinstance(word.start, (int, float))
            or isinstance(word.start, bool)
            or not isinstance(word.end, (int, float))
            or isinstance(word.end, bool)
            or not math.isfinite(word.start)
            or not math.isfinite(word.end)
            or word.start < 0
            or word.end < 0
            or word.end <= word.start
        ):
            raise PackageGenerationError("transcript timestamps are invalid")
        start_milliseconds = round(word.start * 1000)
        end_milliseconds = round(word.end * 1000)
        if start_milliseconds < previous_end or end_milliseconds <= start_milliseconds:
            raise PackageGenerationError("transcript timestamps are invalid")
        cues.append(
            f"{_vtt_timestamp(start_milliseconds)} --> "
            f"{_vtt_timestamp(end_milliseconds)}\n"
            f"{word.word.strip()}"
        )
        previous_end = end_milliseconds
    return ("WEBVTT\n\n" + "\n\n".join(cues) + "\n").encode("utf-8")


def _chapters(request: PackageGenerationInput) -> bytes:
    start = 0.0
    chapters: list[dict[str, object]] = []
    for index, segment in enumerate(request.segments, start=1):
        duration = segment.estimated_duration_seconds
        if (
            not isinstance(duration, (int, float))
            or isinstance(duration, bool)
            or not math.isfinite(duration)
            or duration <= 0
        ):
            raise PackageGenerationError("segment duration is invalid")
        end = start + duration
        chapters.append(
            {
                "segment_id": segment.segment_id,
                "title": f"Segment {index}",
                "start_seconds": start,
                "end_seconds": end,
            }
        )
        start = end
    return _json_bytes({"chapters": chapters})


def _show_notes(request: PackageGenerationInput) -> bytes:
    notes = request.content_manifest.get("show_notes")
    if (
        not isinstance(notes, tuple | list)
        or not notes
        or any(not isinstance(note, str) or not note.strip() for note in notes)
    ):
        raise PackageGenerationError("show notes must be a non-empty sequence of text")
    return (
        "# Show notes\n\n" + "\n".join(note.strip() for note in notes) + "\n"
    ).encode("utf-8")


def build_package_provenance(request: PackageGenerationInput) -> PackageProvenance:
    """Build the immutable package provenance record for a verified request."""
    _validate_request(request)
    transcript = request.final_qa.transcript
    details = {
        "source": {
            "sha256": request.source.source_sha256,
        },
        "script": {
            "id": request.script.script_id,
            "canonical_hash": request.script.canonical_hash,
            "source_sha256": request.script.source_sha256,
            "profile_id": request.script.profile_id,
            "version": request.script_version,
        },
        "profile": {
            "id": request.profile.profile_id,
            "version": request.profile.version,
        },
        "voice_assets": [
            {
                "speaker_id": speaker.speaker_id,
                "voice_asset_id": speaker.voice_asset_id,
            }
            for speaker in request.profile.speakers
        ],
        "provider": {
            "provider": transcript.provider,
            "model": transcript.model,
            "request_id": transcript.request_id,
        },
        "usage": {
            "input_units": transcript.usage.input_units,
            "output_units": transcript.usage.output_units,
        },
        "cost": str(transcript.cost),
        "lexicon": _json_value(request.lexicon_evidence),
        "mastering": _json_value(
            {
                "profile_version": request.mastered_audio.provenance.profile_version,
                "ffmpeg_executable": (
                    request.mastered_audio.provenance.ffmpeg_executable
                ),
                "ffmpeg_version": request.mastered_audio.provenance.ffmpeg_version,
                "command": request.mastered_audio.provenance.command,
                "commands": request.mastered_audio.provenance.commands,
                "filters": request.mastered_audio.provenance.filters,
                "input_checksums": request.mastered_audio.provenance.input_checksums,
                "wav_checksum": request.mastered_audio.provenance.wav_checksum,
                "mp3_checksum": request.mastered_audio.provenance.mp3_checksum,
                "wav_metadata": request.mastered_audio.provenance.wav_metadata,
                "mp3_metadata": request.mastered_audio.provenance.mp3_metadata,
            }
        ),
        "workflow": _json_value(
            {
                "episode_version_id": str(request.episode_version_id),
                **dict(request.render_evidence),
            }
        ),
        "qa": _json_value(request.final_qa.to_dict()),
    }
    return PackageProvenance(
        source_sha256=request.source.source_sha256,
        script_version=request.script_version,
        profile_version=request.profile.version,
        renderer=request.renderer,
        qa="pass",
        critical_token_accuracy=request.final_qa.critical_token_accuracy,
        final_sha256=sha256(request.mastered_audio.wav_bytes).hexdigest(),
        details=details,
    )


def generate_package_artifacts(
    request: PackageGenerationInput,
) -> tuple[PackageArtifact, ...]:
    """Generate all canonical package files without external side effects."""
    _validate_request(request)
    master = request.mastered_audio
    checksums = {
        "wav": sha256(master.wav_bytes).hexdigest(),
        "mp3": sha256(master.mp3_bytes).hexdigest(),
    }
    payloads = {
        "episode.wav": master.wav_bytes,
        "episode.mp3": master.mp3_bytes,
        "transcript.txt": (request.final_qa.transcript.text.rstrip("\n") + "\n").encode(
            "utf-8"
        ),
        "transcript.vtt": _transcript_vtt(request),
        "chapters.json": _chapters(request),
        "show-notes.md": _show_notes(request),
        "qa-report.json": _json_bytes(request.final_qa.to_dict()),
        "provenance.json": _json_bytes(build_package_provenance(request).to_dict()),
        "render-manifest.json": _json_bytes(
            {
                "content_manifest": request.content_manifest,
                "segments": [segment.segment_id for segment in request.segments],
                "render_evidence": request.render_evidence,
                "master_checksums": checksums,
            }
        ),
    }
    return tuple(
        PackageArtifact(name, _MEDIA_TYPES[name], payloads[name])
        for name in REQUIRED_PACKAGE_ARTIFACTS
    )


def build_package_artifacts(
    request: PackageGenerationInput,
) -> tuple[PackageArtifact, ...]:
    """Compatibility spelling for :func:`generate_package_artifacts`."""
    return generate_package_artifacts(request)


__all__ = [
    "PackageGenerationError",
    "PackageGenerationInput",
    "build_package_artifacts",
    "build_package_provenance",
    "generate_package_artifacts",
]
