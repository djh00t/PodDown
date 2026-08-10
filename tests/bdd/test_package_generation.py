"""Executable acceptance tests for deterministic package artifact generation."""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
from importlib import import_module
from uuid import UUID

from pytest_bdd import given, scenarios, then, when

from poddown.artifacts import FilesystemArtifactStore
from poddown.audio.diagnostics import AudioDiagnostics
from poddown.audio.mastering import MasteredAudio, MasteringProvenance
from poddown.content.models import Profile, ScriptTurn, ScriptVersion, SpeakerProfile
from poddown.content.segmentation import Segment
from poddown.content.source import snapshot_source
from poddown.domain import FidelityResult, ProviderUsage
from poddown.packages import EpisodePackage, EpisodePackageService
from poddown.providers.contracts import TranscriptResult, TranscriptWord
from poddown.qa.final_master import FinalMasterGate, FinalMasterQaResult

scenarios("../features/package_generation.feature")

EPISODE_VERSION_ID = UUID("123e4567-e89b-12d3-a456-426614174000")
MASTER_WAV = b"RIFF deterministic verified WAV bytes"
MASTER_MP3 = b"ID3 deterministic verified MP3 bytes"
TRANSCRIPT_TEXT = "PodDown produces a verified episode."


def _generation_module():
    """Load the production boundary only when a scenario executes."""
    try:
        return import_module("poddown.package_generation")
    except ModuleNotFoundError as error:
        raise AssertionError(
            "Package artifact generation is not implemented"
        ) from error


def _source_and_script() -> tuple[object, ScriptVersion]:
    """Build source-bound script fixtures using the repository's immutable models."""
    source = snapshot_source(
        "# Fixture episode\n\nPodDown produces\n\na verified episode.\n"
    )
    first = source.blocks[1]
    second = source.blocks[2]
    from poddown.content.models import SourceAnchor

    first_anchor = SourceAnchor(first.block_id, first.start, first.end)
    second_anchor = SourceAnchor(second.block_id, second.start, second.end)
    script = ScriptVersion(
        script_id="script-fixture-v1",
        source_sha256=source.source_sha256,
        profile_id="spoken-word",
        turns=(
            ScriptTurn(
                "turn-1",
                "host",
                "PodDown produces",
                "factual",
                (first_anchor,),
                (first_anchor,),
            ),
            ScriptTurn(
                "turn-2",
                "host",
                "a verified episode.",
                "factual",
                (second_anchor,),
                (second_anchor,),
            ),
        ),
        canonical_hash=sha256(b"script-fixture-v1").hexdigest(),
    )
    return source, script


def _profile() -> Profile:
    """Build a minimal valid immutable spoken-word profile."""
    return Profile(
        profile_id="spoken-word",
        version="spoken-word-v1",
        format_type="narration",
        target_minutes=1,
        speakers=(SpeakerProfile("host", "Host", "voice-host"),),
        style={},
        audio={},
        quality={},
        document_overridable=frozenset(),
    )


def _segments(source) -> tuple[Segment, ...]:
    """Build hand-timed ordered segments for deterministic chapter assertions."""
    _, first, second = source.blocks
    from poddown.content.models import SourceAnchor

    return (
        Segment(
            "segment-001",
            ("turn-1",),
            ("host",),
            "PodDown produces",
            (SourceAnchor(first.block_id, first.start, first.end),),
            (),
            "",
            "a verified episode.",
            1.25,
            "normal",
        ),
        Segment(
            "segment-002",
            ("turn-2",),
            ("host",),
            "a verified episode.",
            (SourceAnchor(second.block_id, second.start, second.end),),
            (),
            "PodDown produces",
            "",
            2.5,
            "normal",
        ),
    )


def _mastered_audio() -> MasteredAudio:
    """Build verified master evidence with byte-bound WAV and MP3 checksums."""
    diagnostics = AudioDiagnostics(44_100, 1, 3.75, 0.1, 0.0, 0.0)
    return MasteredAudio(
        "episode-fixture",
        "v1",
        MASTER_WAV,
        MASTER_MP3,
        diagnostics,
        MasteringProvenance(
            "spoken-word-v1",
            "fake-ffmpeg",
            "fake-1.0",
            ("fake-ffmpeg", "-i", "input.wav"),
            ("loudnorm",),
            (sha256(b"segment-input").hexdigest(),),
            sha256(MASTER_WAV).hexdigest(),
            sha256(MASTER_MP3).hexdigest(),
            diagnostics.to_dict(),
            {"codec_name": "mp3"},
        ),
    )


def _final_qa(master: MasteredAudio, *, passed: bool = True) -> FinalMasterQaResult:
    """Build real final-QA data, including timestamped transcript evidence."""
    words = (
        TranscriptWord("PodDown", 0.0, 0.5),
        TranscriptWord("produces", 0.5, 1.2),
        TranscriptWord("a", 1.2, 1.4),
        TranscriptWord("verified", 1.4, 2.2),
        TranscriptWord("episode.", 2.2, 3.0),
    )
    transcript = TranscriptResult(
        TRANSCRIPT_TEXT,
        words,
        "fake-transcriber",
        "fake-model-v1",
        ProviderUsage(12, 5),
        "request-001",
        sha256(master.wav_bytes).hexdigest(),
        Decimal("0.0042"),
        mode="test",
    )
    fidelity = FidelityResult(passed, 1.0 if passed else 0.5, "none")
    gates = (
        FinalMasterGate("audio", passed, {"sample_rate_hz": 44_100}),
        FinalMasterGate("critical_tokens", passed, {"accuracy": fidelity.accuracy}),
    )
    return FinalMasterQaResult(
        master.episode_id,
        sha256(master.wav_bytes).hexdigest(),
        transcript,
        fidelity,
        gates,
        master.diagnostics,
    )


def _request(*, qa_passed: bool = True):
    """Construct the target package-generation input with fixed source evidence."""
    generation = _generation_module()
    source, script = _source_and_script()
    master = _mastered_audio()
    return generation.PackageGenerationInput(
        episode_version_id=EPISODE_VERSION_ID,
        source=source,
        profile=_profile(),
        script=script,
        script_version=1,
        segments=_segments(source),
        content_manifest={
            "script": {"canonical_hash": script.canonical_hash},
            "show_notes": ("PodDown produces",),
        },
        mastered_audio=master,
        final_qa=_final_qa(master, passed=qa_passed),
        render_evidence={"workflow_id": "render-001", "attempt": 1},
        renderer="fake-renderer-v1",
        lexicon_evidence={"version": "lexicon-v1", "entries": 1},
    )


def _artifact_bytes(artifacts) -> dict[str, bytes]:
    """Index generated package artifact payloads by their canonical names."""
    return {artifact.name: artifact.data for artifact in artifacts}


@given("a passing package-generation request")
def passing_package_generation_request(context):
    context.values["request"] = _request()


@given("a package-generation request with failed final-master QA")
def request_with_failed_final_master_qa(context):
    context.values["request"] = _request(qa_passed=False)


@given("a package-generation request with no transcript words")
def request_with_no_transcript_words(context):
    request = _request()
    final_qa = replace(
        request.final_qa,
        transcript=replace(request.final_qa.transcript, words=()),
    )
    context.values["request"] = replace(request, final_qa=final_qa)


@given("a package-generation request with non-monotonic transcript timestamps")
def request_with_non_monotonic_transcript_timestamps(context):
    request = _request()
    words = list(request.final_qa.transcript.words)
    object.__setattr__(words[1], "start", 0.25)
    final_qa = replace(
        request.final_qa,
        transcript=replace(request.final_qa.transcript, words=tuple(words)),
    )
    context.values["request"] = replace(request, final_qa=final_qa)


@given("a package-generation request with a mismatched master checksum")
def request_with_mismatched_master_checksum(context):
    request = _request()
    provenance = replace(request.mastered_audio.provenance, wav_checksum="0" * 64)
    context.values["request"] = replace(
        request, mastered_audio=replace(request.mastered_audio, provenance=provenance)
    )


@given("a package-generation request with non-JSON render evidence")
def request_with_non_json_render_evidence(context):
    try:
        context.values["request"] = replace(
            _request(), render_evidence={"bad": object()}
        )
    except Exception as error:
        context.values["error"] = error


@given("a package-generation request with incomplete critical-token accuracy")
def request_with_incomplete_critical_token_accuracy(context):
    request = _request()
    context.values["request"] = replace(
        request,
        final_qa=replace(
            request.final_qa,
            fidelity=replace(request.final_qa.fidelity, accuracy=0.99),
        ),
    )


@given("a package-generation request with a detached segment")
def request_with_detached_segment(context):
    request = _request()
    context.values["request"] = replace(
        request,
        segments=(replace(request.segments[0], text="Forged package narration."),),
    )


@given("a package-generation request with unverified show notes")
def request_with_unverified_show_notes(context):
    request = _request()
    context.values["request"] = replace(
        request,
        content_manifest={
            "script": {"canonical_hash": request.script.canonical_hash},
            "show_notes": ("Unsupported factual claim.",),
        },
    )


@given("two equivalent package-generation requests")
def equivalent_package_generation_requests(context):
    context.values["first_request"] = _request()
    context.values["second_request"] = _request()


@when("package artifacts are built")
def package_artifacts_are_built(context):
    if "error" in context.values:
        return
    try:
        context.values["artifacts"] = _generation_module().build_package_artifacts(
            context.values["request"]
        )
    except Exception as error:
        context.values["error"] = error


@when("package artifacts are built twice")
def package_artifacts_are_built_twice(context):
    generation = _generation_module()
    context.values["first_artifacts"] = generation.build_package_artifacts(
        context.values["request"]
    )
    context.values["second_artifacts"] = generation.build_package_artifacts(
        context.values["request"]
    )


@when("package provenance and artifacts are built")
def package_provenance_and_artifacts_are_built(context):
    generation = _generation_module()
    context.values["provenance"] = generation.build_package_provenance(
        context.values["request"]
    )
    context.values["artifacts"] = generation.build_package_artifacts(
        context.values["request"]
    )


@when("package artifacts are built for both requests")
def package_artifacts_are_built_for_both_requests(context):
    generation = _generation_module()
    context.values["first_artifacts"] = generation.build_package_artifacts(
        context.values["first_request"]
    )
    context.values["second_artifacts"] = generation.build_package_artifacts(
        context.values["second_request"]
    )


@when("generated package artifacts are committed and replayed")
def generated_package_artifacts_are_committed_and_replayed(context, tmp_path):
    request = context.values["request"]
    generation = _generation_module()
    artifacts = generation.build_package_artifacts(request)
    provenance = generation.build_package_provenance(request)
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    service = EpisodePackageService(store, tmp_path / "packages")
    first = service.commit(request.episode_version_id, artifacts, provenance)
    second = service.commit(request.episode_version_id, artifacts, provenance)
    context.values["first_package"] = first
    context.values["second_package"] = second
    context.values["artifact_store"] = store


@then("all nine canonical package artifacts contain the expected deterministic bytes")
def all_nine_artifacts_contain_expected_bytes(context):
    artifacts = _artifact_bytes(context.values["artifacts"])
    assert tuple(artifacts) == (
        "episode.wav",
        "episode.mp3",
        "transcript.txt",
        "transcript.vtt",
        "chapters.json",
        "show-notes.md",
        "qa-report.json",
        "provenance.json",
        "render-manifest.json",
    )
    assert artifacts["episode.wav"] == MASTER_WAV
    assert artifacts["episode.mp3"] == MASTER_MP3
    assert artifacts["transcript.txt"] == b"PodDown produces a verified episode.\n"
    assert artifacts["show-notes.md"] == b"# Show notes\n\nPodDown produces\n"
    assert (
        json.loads(artifacts["qa-report.json"])
        == context.values["request"].final_qa.to_dict()
    )


@then("the transcript VTT contains one monotonic three-decimal cue per word")
def transcript_vtt_contains_monotonic_cues(context):
    artifacts = _artifact_bytes(context.values["first_artifacts"])
    assert artifacts["transcript.vtt"] == (
        b"WEBVTT\n\n"
        b"00:00:00.000 --> 00:00:00.500\nPodDown\n\n"
        b"00:00:00.500 --> 00:00:01.200\nproduces\n\n"
        b"00:00:01.200 --> 00:00:01.400\na\n\n"
        b"00:00:01.400 --> 00:00:02.200\nverified\n\n"
        b"00:00:02.200 --> 00:00:03.000\nepisode.\n"
    )
    assert (
        _artifact_bytes(context.values["second_artifacts"])["transcript.vtt"]
        == artifacts["transcript.vtt"]
    )


@then("chapters retain segment order and cumulative estimated durations")
def chapters_retain_order_and_duration(context):
    chapters = json.loads(
        _artifact_bytes(context.values["first_artifacts"])["chapters.json"]
    )
    assert chapters == {
        "chapters": [
            {
                "segment_id": "segment-001",
                "title": "Segment 1",
                "start_seconds": 0.0,
                "end_seconds": 1.25,
            },
            {
                "segment_id": "segment-002",
                "title": "Segment 2",
                "start_seconds": 1.25,
                "end_seconds": 3.75,
            },
        ]
    }


@then("provenance and the render manifest contain sorted source-to-QA evidence")
def provenance_and_render_manifest_contain_evidence(context):
    artifacts = _artifact_bytes(context.values["artifacts"])
    provenance = json.loads(artifacts["provenance.json"])
    manifest = json.loads(artifacts["render-manifest.json"])
    assert context.values["provenance"].final_sha256 == sha256(MASTER_WAV).hexdigest()
    assert provenance["source_sha256"] == context.values["request"].source.source_sha256
    assert provenance["renderer"] == "fake-renderer-v1"
    assert provenance["details"]["lexicon"]["version"] == "lexicon-v1"
    assert provenance["details"]["usage"] == {"input_units": 12, "output_units": 5}
    assert provenance["details"]["cost"] == "0.0042"
    assert manifest["segments"] == ["segment-001", "segment-002"]
    assert manifest["render_evidence"] == {"attempt": 1, "workflow_id": "render-001"}
    assert (
        artifacts["provenance.json"]
        == json.dumps(provenance, sort_keys=True, separators=(",", ":")).encode()
    )
    assert (
        artifacts["render-manifest.json"]
        == json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    )


@then("package generation fails closed")
def package_generation_fails_closed(context):
    assert isinstance(context.values.get("error"), Exception)


@then("every generated artifact payload is byte-identical")
def every_generated_artifact_payload_is_byte_identical(context):
    first = _artifact_bytes(context.values["first_artifacts"])
    second = _artifact_bytes(context.values["second_artifacts"])
    assert first == second


@then("the committed package manifest and bytes replay identically")
def committed_package_replays_identically(context):
    first = context.values["first_package"]
    second = context.values["second_package"]
    assert first == second
    assert EpisodePackage.from_dict(first.to_dict()) == first
    request = context.values["request"]
    expected = {
        artifact.name: artifact.data
        for artifact in _generation_module().build_package_artifacts(request)
    }
    store = context.values["artifact_store"]
    for reference in first.files:
        assert store.read(reference) == expected[reference.name]
