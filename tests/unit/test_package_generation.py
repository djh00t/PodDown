"""Unit contracts for deterministic immutable package generation."""

from __future__ import annotations

import json
import wave
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from typing import cast
from uuid import UUID

import pytest

from poddown.audio.diagnostics import diagnose_wav
from poddown.audio.mastering import MasteredAudio, MasteringProvenance
from poddown.content.models import (
    Profile,
    ScriptTurn,
    ScriptVersion,
    SourceAnchor,
    SpeakerProfile,
)
from poddown.content.segmentation import Segment
from poddown.content.source import snapshot_source
from poddown.domain import FidelityResult, ProviderUsage
from poddown.package_generation import (
    PackageGenerationError,
    PackageGenerationInput,
    generate_package_artifacts,
)
from poddown.providers.contracts import TranscriptResult, TranscriptWord
from poddown.qa.final_master import FinalMasterGate, FinalMasterQaResult

EPISODE_VERSION_ID = UUID("123e4567-e89b-12d3-a456-426614174000")


def _wav_bytes() -> bytes:
    stream = BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(44_100)
        output.writeframes(b"\x00\x00" * 882)
    return stream.getvalue()


def _input() -> PackageGenerationInput:
    source = snapshot_source("# Episode title\n\nSource-backed first note.\n")
    anchor = SourceAnchor(
        block_id=source.blocks[1].block_id,
        start=source.blocks[1].start,
        end=source.blocks[1].end,
    )
    profile = Profile(
        profile_id="spoken-word",
        version="spoken-word-v1",
        format_type="narration",
        target_minutes=5,
        speakers=(SpeakerProfile("host", "Host", "voice-host"),),
        style={},
        audio={},
        quality={},
        document_overridable=frozenset(),
    )
    script = ScriptVersion(
        script_id="script-1",
        source_sha256=source.source_sha256,
        profile_id=profile.profile_id,
        turns=(
            ScriptTurn(
                turn_id="turn-1",
                speaker_id="host",
                text="Welcome to PodDown.",
                kind="factual",
                source_anchors=(anchor,),
                claim_anchors=(anchor,),
            ),
        ),
        canonical_hash="a" * 64,
    )
    segments = (
        Segment(
            segment_id="segment-1",
            turn_ids=("turn-1",),
            speaker_ids=("host",),
            text="Welcome to PodDown.",
            source_anchors=(anchor,),
            critical_tokens=(),
            leading_context="",
            trailing_context="",
            estimated_duration_seconds=1.25,
            difficulty="normal",
        ),
        Segment(
            segment_id="segment-2",
            turn_ids=("turn-2",),
            speaker_ids=("host",),
            text="The package is immutable.",
            source_anchors=(anchor,),
            critical_tokens=(),
            leading_context="",
            trailing_context="",
            estimated_duration_seconds=2.5,
            difficulty="normal",
        ),
    )
    wav = _wav_bytes()
    mp3 = b"ID3\x04\x00deterministic-master"
    diagnostics = diagnose_wav(
        wav,
        expected_sample_rate_hz=44_100,
        expected_channels=1,
        max_duration_seconds=10.0,
    )
    master = MasteredAudio(
        episode_id="episode-1",
        episode_version="v1",
        wav_bytes=wav,
        mp3_bytes=mp3,
        diagnostics=diagnostics,
        provenance=MasteringProvenance(
            profile_version=profile.version,
            ffmpeg_executable="ffmpeg",
            ffmpeg_version="6.1",
            command=("ffmpeg", "-i", "input.wav"),
            filters=("loudnorm",),
            input_checksums=("b" * 64,),
            wav_checksum=sha256(wav).hexdigest(),
            mp3_checksum=sha256(mp3).hexdigest(),
            wav_metadata=diagnostics.to_dict(),
            mp3_metadata={"codec_name": "mp3"},
        ),
    )
    transcript = TranscriptResult(
        text="Welcome to PodDown.",
        words=(
            TranscriptWord("Welcome", 0.0, 0.5),
            TranscriptWord("to", 0.5, 0.75),
            TranscriptWord("PodDown.", 0.75, 1.25),
        ),
        provider="fixture-transcriber",
        model="fixture-stt-v1",
        usage=ProviderUsage(12, 3),
        request_id="request-1",
        checksum=sha256(wav).hexdigest(),
        cost=Decimal("0.0042"),
    )
    qa = FinalMasterQaResult(
        subject_id="episode-1",
        master_checksum=sha256(wav).hexdigest(),
        transcript=transcript,
        fidelity=FidelityResult(True, 1.0, "none"),
        gates=(FinalMasterGate("audio", True, {"peak": 0.1}),),
        diagnostics=diagnostics,
    )
    return PackageGenerationInput(
        episode_version_id=EPISODE_VERSION_ID,
        source=source,
        profile=profile,
        script=script,
        script_version=7,
        segments=segments,
        content_manifest={
            "script": {"canonical_hash": script.canonical_hash},
            "show_notes": ("Source-backed first note.",),
        },
        mastered_audio=master,
        final_qa=qa,
        render_evidence={"workflow_id": "render-1", "attempt": 1},
        renderer="local-deterministic-v1",
        lexicon_evidence={"version": "lexicon-v1"},
    )


def _artifacts(value: PackageGenerationInput) -> dict[str, bytes]:
    return {
        artifact.name: artifact.data for artifact in generate_package_artifacts(value)
    }


def test_generation_preserves_exact_master_audio_and_transcript_bytes():
    """Replacing master or transcript bytes must change the assembled package."""
    value = _input()

    artifacts = _artifacts(value)

    assert artifacts["episode.wav"] == value.mastered_audio.wav_bytes
    assert artifacts["episode.mp3"] == value.mastered_audio.mp3_bytes
    assert artifacts["transcript.txt"] == b"Welcome to PodDown.\n"


def test_generation_formats_each_timestamped_transcript_word_as_stable_webvtt():
    """Changing cue precision, ordering, or text must break this subtitle contract."""
    artifacts = _artifacts(_input())

    assert artifacts["transcript.vtt"] == (
        b"WEBVTT\n\n"
        b"00:00:00.000 --> 00:00:00.500\nWelcome\n\n"
        b"00:00:00.500 --> 00:00:00.750\nto\n\n"
        b"00:00:00.750 --> 00:00:01.250\nPodDown.\n"
    )


def test_generation_rejects_timestamp_ranges_that_round_to_zero_duration():
    """Distinct sub-millisecond evidence must not become invalid WebVTT."""
    value = _input()
    transcript = replace(
        value.final_qa.transcript,
        words=(TranscriptWord("tiny", 0.0001, 0.0004),),
    )

    with pytest.raises(PackageGenerationError):
        generate_package_artifacts(
            replace(value, final_qa=replace(value.final_qa, transcript=transcript))
        )


def test_generation_normalizes_transcript_to_one_trailing_newline():
    """Replayable transcript text has exactly one terminal line ending."""
    value = _input()
    transcript = replace(value.final_qa.transcript, text="Welcome to PodDown.\n\n")

    artifacts = _artifacts(
        replace(value, final_qa=replace(value.final_qa, transcript=transcript))
    )

    assert artifacts["transcript.txt"] == b"Welcome to PodDown.\n"


@pytest.mark.parametrize(
    "words",
    (
        (),
        (TranscriptWord("later", 1.0, 1.2), TranscriptWord("early", 0.9, 1.0)),
        (TranscriptWord("first", 0.0, 1.0), TranscriptWord("overlap", 0.9, 1.2)),
    ),
)
def test_generation_rejects_absent_non_monotonic_or_overlapping_word_timestamps(words):
    """Dropping timestamp evidence or accepting ambiguous cue order is unsafe."""
    value = _input()
    qa = replace(
        value.final_qa, transcript=replace(value.final_qa.transcript, words=words)
    )

    with pytest.raises(PackageGenerationError):
        generate_package_artifacts(replace(value, final_qa=qa))


def test_generation_emits_cumulative_segment_chapters_and_source_anchored_notes_only():
    """Changing segment offsets or adding unsupported prose must be observable."""
    artifacts = _artifacts(_input())

    assert artifacts["chapters.json"] == (
        b'{"chapters":[{"end_seconds":1.25,"segment_id":"segment-1",'
        b'"start_seconds":0.0,"title":"Segment 1"},{"end_seconds":3.75,'
        b'"segment_id":"segment-2","start_seconds":1.25,"title":"Segment 2"}]}'
    )
    assert artifacts["show-notes.md"] == (
        b"# Show notes\n\nSource-backed first note.\n"
    )


def test_generation_serializes_qa_provenance_and_render_manifest_compactly_and_sorted():
    """Changing evidence retention or JSON canonicalization must fail this contract."""
    artifacts = _artifacts(_input())

    assert artifacts["qa-report.json"] == (
        b'{"critical_token_accuracy":1.0,"gates":[{"evidence":{"peak":0.1},'
        b'"name":"audio","passed":true}],"passed":true,"scores":'
        b'{"critical_token_accuracy":1.0},"stage":"master","subject_id":"episode-1"}'
    )
    provenance = json.loads(artifacts["provenance.json"])
    assert provenance["source_sha256"] == _input().source.source_sha256
    assert provenance["script_version"] == 7
    assert provenance["profile_version"] == "spoken-word-v1"
    assert provenance["renderer"] == "local-deterministic-v1"
    assert provenance["details"]["usage"] == {
        "input_units": 12,
        "output_units": 3,
    }
    assert provenance["details"]["cost"] == "0.0042"
    assert provenance["details"]["lexicon"] == {"version": "lexicon-v1"}
    manifest = json.loads(artifacts["render-manifest.json"])
    assert manifest["content_manifest"] == {
        "script": {"canonical_hash": "a" * 64},
        "show_notes": ["Source-backed first note."],
    }
    assert manifest["segments"] == ["segment-1", "segment-2"]
    assert manifest["render_evidence"] == {"attempt": 1, "workflow_id": "render-1"}
    assert manifest["master_checksums"] == {
        "mp3": sha256(_input().mastered_audio.mp3_bytes).hexdigest(),
        "wav": sha256(_input().mastered_audio.wav_bytes).hexdigest(),
    }
    for name in ("qa-report.json", "provenance.json", "render-manifest.json"):
        assert (
            artifacts[name]
            == json.dumps(
                json.loads(artifacts[name]), sort_keys=True, separators=(",", ":")
            ).encode()
        )


def test_generation_requires_script_content_manifest_binding():
    """A package cannot omit the canonical script identity from its manifest."""
    value = _input()

    with pytest.raises(PackageGenerationError):
        generate_package_artifacts(
            replace(
                value,
                content_manifest={"show_notes": ("Source-backed first note.",)},
            )
        )


def test_generation_requires_perfect_critical_token_accuracy():
    """Passing soft QA gates cannot waive the terminal critical-token hard gate."""
    value = _input()
    fidelity = replace(value.final_qa.fidelity, accuracy=0.99)

    with pytest.raises(PackageGenerationError):
        generate_package_artifacts(
            replace(value, final_qa=replace(value.final_qa, fidelity=fidelity))
        )


@pytest.mark.parametrize(
    "replacement",
    (
        {"source": object()},
        {"profile": object()},
        {"script": object()},
        {"script_version": 0},
        {"renderer": ""},
        {"segments": ()},
    ),
)
def test_generation_input_rejects_missing_canonical_identity(replacement):
    """Removing any immutable identity must stop generation before artifact output."""
    with pytest.raises((PackageGenerationError, TypeError, ValueError)):
        replace(_input(), **replacement)


def test_generation_input_is_immutable_after_validation():
    """Mutating a validated generation request would invalidate replay evidence."""
    value = _input()

    with pytest.raises(FrozenInstanceError):
        value.renderer = "different-renderer"  # type: ignore[misc]


def test_generation_rejects_detached_content_and_duplicate_segment_ids():
    """Detached content provenance could otherwise create a replayable false package."""
    value = _input()
    duplicate = replace(value.segments[1], segment_id=value.segments[0].segment_id)
    invalid_values = (
        replace(value, script=replace(value.script, source_sha256="b" * 64)),
        replace(value, script=replace(value.script, profile_id="other-profile")),
        replace(value, script=replace(value.script, canonical_hash="b" * 64)),
    )

    for invalid in invalid_values:
        with pytest.raises(PackageGenerationError):
            generate_package_artifacts(invalid)

    with pytest.raises(PackageGenerationError):
        replace(value, segments=(value.segments[0], duplicate))


def test_generation_rejects_failed_final_qa_and_unbound_master_checksums():
    """A failed QA decision or checksum mismatch must prevent package creation."""
    value = _input()
    failed_qa = replace(
        value.final_qa,
        gates=(FinalMasterGate("audio", False, {"peak": 1.1}),),
    )
    unbound_master = replace(
        value.mastered_audio,
        provenance=replace(value.mastered_audio.provenance, wav_checksum="c" * 64),
    )

    for invalid in (
        replace(value, final_qa=failed_qa),
        replace(value, mastered_audio=unbound_master),
    ):
        with pytest.raises(PackageGenerationError):
            generate_package_artifacts(invalid)


@pytest.mark.parametrize(
    "field", ("content_manifest", "render_evidence", "lexicon_evidence")
)
def test_generation_rejects_non_json_serializable_evidence(field):
    """Unserializable evidence must fail before a partial immutable package exists."""
    with pytest.raises(PackageGenerationError):
        generate_package_artifacts(replace(_input(), **{field: {"invalid": object()}}))


def test_equivalent_immutable_requests_generate_byte_identical_artifacts():
    """Equivalent input snapshots must replay without byte-level package drift."""
    first = _artifacts(_input())
    second = _artifacts(cast(PackageGenerationInput, replace(_input())))

    assert second == first
