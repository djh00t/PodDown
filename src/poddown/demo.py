"""Repeatable deterministic-local reference episode composition."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import cast
from uuid import UUID

import yaml

from poddown.agent_mcp import AgentMCPServer, AuthenticatedContext, LocalGateway
from poddown.artifacts import FilesystemArtifactStore as PackageArtifactStore
from poddown.audio import (
    DeterministicLocalRenderer,
    DurableRenderService,
    RenderRequest,
)
from poddown.audio import (
    VoiceConsent as AudioVoiceConsent,
)
from poddown.audio.contracts import RenderedAudio, RenderOutcome
from poddown.audio.diagnostics import diagnose_wav
from poddown.audio.mastering import (
    FfmpegRunner,
    MasteringProfile,
    MasteringRequest,
    MasteringSegment,
    MasteringService,
)
from poddown.audio.render import RenderRejectedError
from poddown.audio.selection import CandidateQuality, select_candidate
from poddown.audio.storage import FilesystemArtifactStore, FilesystemRenderRecordStore
from poddown.content.adaptation import (
    AdaptationProposal,
    EpisodeTreatment,
    FixtureReasoningPort,
)
from poddown.content.lexicon import PronunciationEntry, PronunciationLexicon
from poddown.content.models import (
    ScriptTurn,
    SourceAnchor,
    SourceSnapshot,
    VoiceAsset,
    VoiceConsent,
)
from poddown.content.segmentation import SegmentationCapabilities
from poddown.content.service import (
    ContentPreparationRequest,
    ContentPreparationResult,
    prepare_content,
)
from poddown.content.source import snapshot_source
from poddown.domain import ProviderUsage
from poddown.package_generation import (
    PackageGenerationInput,
    generate_package_artifacts,
)
from poddown.packages import (
    REQUIRED_PACKAGE_ARTIFACTS,
    EpisodePackageService,
)
from poddown.persistence import SQLiteUsageLedger, UsageEvent
from poddown.providers.contracts import TranscriptResult, TranscriptWord
from poddown.publishing import (
    DisclosurePolicy,
    FilesystemPublicationAdapter,
    PublicationAuthorization,
    PublicationTarget,
    PublishingService,
)
from poddown.qa.fidelity import evaluate_critical_tokens
from poddown.qa.final_master import FinalMasterQaService

_ROOT = Path(__file__).resolve().parents[2]
_TENANT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
_PROJECT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")
_EPISODE_VERSION_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b14")


def _reference_fixture_root() -> Path | Traversable:
    """Resolve source-checkout fixtures or the packaged wheel resources."""
    source_root = _ROOT / "integrations" / "reference-demo" / "v1"
    if source_root.is_dir():
        return source_root
    return resources.files("poddown").joinpath("reference-demo", "v1")


@dataclass(frozen=True, slots=True)
class DemoResult:
    """JSON-safe terminal evidence for one local reference-demo run."""

    mode: str
    source_sha256: str
    profile_id: str
    target_minutes: int
    speakers: tuple[str, ...]
    segment_ids: tuple[str, ...]
    take_count: int
    selected_candidate_ids: tuple[str, ...]
    failed_segment_ids: tuple[str, ...]
    regenerated_segment_ids: tuple[str, ...]
    critical_token_accuracy: float
    package_artifacts: tuple[str, ...]
    package_manifest_sha256: str
    publication_path: str
    usage: Mapping[str, int]
    cost: str
    replayed_takes: int
    mcp_preview: Mapping[str, object]
    voice_bindings: Mapping[str, str]

    def to_dict(self) -> dict[str, object]:
        """Return deterministic JSON-native result evidence."""
        return {
            "cost": self.cost,
            "critical_token_accuracy": self.critical_token_accuracy,
            "failed_segment_ids": list(self.failed_segment_ids),
            "mcp_preview": dict(self.mcp_preview),
            "mode": self.mode,
            "package_artifacts": list(self.package_artifacts),
            "package_manifest_sha256": self.package_manifest_sha256,
            "profile_id": self.profile_id,
            "publication_path": self.publication_path,
            "regenerated_segment_ids": list(self.regenerated_segment_ids),
            "replayed_takes": self.replayed_takes,
            "segment_ids": list(self.segment_ids),
            "selected_candidate_ids": list(self.selected_candidate_ids),
            "source_sha256": self.source_sha256,
            "speakers": list(self.speakers),
            "take_count": self.take_count,
            "target_minutes": self.target_minutes,
            "usage": dict(self.usage),
            "voice_bindings": dict(self.voice_bindings),
        }


@dataclass(frozen=True, slots=True)
class _ReferenceFixture:
    source: str
    profile_yaml: str
    profile: Mapping[str, object]
    adaptation: Mapping[str, object]
    voices: Mapping[str, object]
    disclosure: Mapping[str, object]


class _FailOnceRenderer:
    """Return invalid local audio once so regeneration remains observable."""

    capabilities = DeterministicLocalRenderer.capabilities

    def __init__(self, failed_segment_id: str) -> None:
        self._failed_segment_id = failed_segment_id
        self._failed = False
        self._delegate = DeterministicLocalRenderer()

    async def render(self, request: RenderRequest) -> RenderedAudio:
        if request.segment_id == self._failed_segment_id and not self._failed:
            self._failed = True
            raise RenderRejectedError("intentional_local_render_failure")
        return await self._delegate.render(request)


class _DeterministicTranscriber:
    """Bind deterministic fixture transcript evidence to exact master WAV bytes."""

    def __init__(self, text: str) -> None:
        self._text = text

    async def transcribe(self, audio: bytes) -> TranscriptResult:
        words = tuple(
            TranscriptWord(word, index * 0.1, (index + 1) * 0.1)
            for index, word in enumerate(self._text.split())
        )
        return TranscriptResult(
            text=self._text,
            words=words,
            provider="local-deterministic-demo",
            model="local-transcript-v1",
            usage=ProviderUsage(len(audio), len(words)),
            request_id="local-reference-demo-transcript-v1",
            checksum=hashlib.sha256(audio).hexdigest(),
            cost=Decimal("0"),
            mode="deterministic-local-demo",
        )


def _read_text(path: Path | Traversable) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ValueError(f"missing fixture: {path.name}") from error


def _yaml_mapping(path: Path | Traversable) -> Mapping[str, object]:
    try:
        value = yaml.safe_load(_read_text(path))
    except yaml.YAMLError as error:
        raise ValueError(f"invalid fixture: {path.name}") from error
    if not isinstance(value, Mapping):
        raise ValueError(f"invalid fixture: {path.name}")
    return value


def _json_mapping(path: Path | Traversable) -> Mapping[str, object]:
    try:
        value = json.loads(_read_text(path))
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid fixture: {path.name}") from error
    if not isinstance(value, Mapping):
        raise ValueError(f"invalid fixture: {path.name}")
    return value


def _load_reference_fixture(fixture_root: Path | Traversable) -> _ReferenceFixture:
    """Load and reject malformed or non-consented demo evidence before rendering."""
    root = fixture_root
    source = _read_text(root / "source.md")
    snapshot = snapshot_source(source)
    profile_yaml = _read_text(root / "profile.yaml")
    profile = _yaml_mapping(root / "profile.yaml")
    adaptation = _json_mapping(root / "adaptation.json")
    voices = _yaml_mapping(root / "voices.yaml")
    disclosure = _yaml_mapping(root / "disclosure.yaml")
    poddown = snapshot.frontmatter.get("poddown")
    speakers = profile.get("speakers")
    assets = voices.get("assets")
    if not isinstance(poddown, Mapping) or poddown.get("duration_minutes") != 12:
        raise ValueError("source frontmatter must declare a 12-minute demo")
    if not isinstance(speakers, list) or len(speakers) != 2:
        raise ValueError("profile must declare two speakers")
    if not isinstance(assets, list) or len(assets) != 2:
        raise ValueError("voices must declare two assets")
    asset_ids: set[str] = set()
    for asset in assets:
        if not isinstance(asset, Mapping):
            raise ValueError("voice asset is malformed")
        asset_id = asset.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id:
            raise ValueError("voice asset ID is malformed")
        if asset.get("synthetic") is not True or asset.get("demo_only") is not True:
            raise ValueError("voice asset must be synthetic and demo-only")
        if asset.get("consent_status") != "approved" or not asset.get("consent_id"):
            raise ValueError("voice consent must be approved")
        asset_ids.add(asset_id)
    if any(
        not isinstance(speaker, Mapping)
        or speaker.get("voice_asset_id") not in asset_ids
        for speaker in speakers
    ):
        raise ValueError("profile voice binding is invalid")
    if any(
        type(disclosure.get(name)) is not bool
        for name in ("spoken", "show_notes", "platform")
    ):
        raise ValueError("disclosure policy is incomplete")
    disclosure_text = disclosure.get("text")
    if (
        disclosure.get("show_notes") is not True
        or not isinstance(disclosure_text, str)
        or not disclosure_text.strip()
    ):
        raise ValueError("disclosure policy is incomplete")
    if not isinstance(adaptation.get("source_turns"), list):
        raise ValueError("adaptation turns are missing")
    return _ReferenceFixture(
        source, profile_yaml, profile, adaptation, voices, disclosure
    )


def _anchor(snapshot: SourceSnapshot, text: str) -> SourceAnchor:
    encoded = text.encode("utf-8")
    start = snapshot.source.encode("utf-8").index(encoded)
    block = next(block for block in snapshot.blocks if block.start <= start < block.end)
    return SourceAnchor(block.block_id, block.start, block.end)


def _build_content_request(fixture: _ReferenceFixture) -> ContentPreparationRequest:
    """Build the existing typed source-bound content preparation request."""
    snapshot = snapshot_source(fixture.source)
    raw_turn_rows = fixture.adaptation.get("source_turns")
    raw_claims = fixture.adaptation.get("claims")
    if (
        not isinstance(raw_turn_rows, list)
        or not all(isinstance(item, Mapping) for item in raw_turn_rows)
        or not isinstance(raw_claims, list)
        or not all(isinstance(item, Mapping) for item in raw_claims)
    ):
        raise ValueError("adaptation turns are malformed")
    turn_rows = cast(list[Mapping[str, object]], raw_turn_rows)
    claims: dict[str, Mapping[str, object]] = {}
    for item in cast(list[Mapping[str, object]], raw_claims):
        claim_anchor = item.get("claim_anchor")
        if not isinstance(claim_anchor, str):
            raise ValueError("adaptation claim anchor is malformed")
        claims[claim_anchor] = item
    turns = tuple(
        ScriptTurn(
            str(row["turn_id"]),
            str(row["speaker_id"]),
            str(claims[str(row["claim_anchor"])]["adapted_value"]),
            "factual",
            (_anchor(snapshot, str(claims[str(row["claim_anchor"])]["source_value"])),),
            (_anchor(snapshot, str(claims[str(row["claim_anchor"])]["source_value"])),),
        )
        for row in turn_rows
    )
    speakers = fixture.profile["speakers"]
    if not isinstance(speakers, list) or not all(
        isinstance(item, Mapping) for item in speakers
    ):
        raise ValueError("profile speakers are malformed")
    treatment = EpisodeTreatment(
        "reference-demo-v1",
        "dialogue",
        ("evidence", "disagreement"),
        12,
        tuple(turn.turn_id for turn in turns),
        {str(item["speaker_id"]): "presenter" for item in speakers},
        tuple(turn.source_anchors[0] for turn in turns),
        tuple(turn.turn_id for turn in turns),
    )
    pronunciation_rows = (
        ("LiDAR", "LIE-dar"),
        ("C1", "see one"),
        ("99.7%", "ninety-nine point seven percent"),
    )
    entries = tuple(
        PronunciationEntry(
            f"reference-{index}",
            source_form,
            spoken_form,
            "reference-demo-lexicon-v1",
            "technical_term",
        )
        for index, (source_form, spoken_form) in enumerate(pronunciation_rows, 1)
    )
    assets = fixture.voices["assets"]
    if not isinstance(assets, list) or not all(
        isinstance(item, Mapping) for item in assets
    ):
        raise ValueError("voice assets are malformed")
    return ContentPreparationRequest(
        markdown=fixture.source,
        profile_yaml=fixture.profile_yaml,
        treatment=treatment,
        reasoning=FixtureReasoningPort(
            {snapshot.source_sha256: AdaptationProposal(treatment, turns)}, {}
        ),
        lexicon_layers={
            "episode": PronunciationLexicon(
                "episode", "reference-demo-lexicon-v1", entries
            )
        },
        capabilities=SegmentationCapabilities(
            240, 10, frozenset(str(item["speaker_id"]) for item in speakers)
        ),
        voice_assets=tuple(VoiceAsset(str(item["asset_id"]), True) for item in assets),
        consents=tuple(VoiceConsent(str(item["asset_id"]), True) for item in assets),
    )


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    temporary.replace(path)


def _write_status(output_dir: Path, stage: str, evidence: Mapping[str, object]) -> None:
    """Atomically persist stage evidence under the caller-selected directory."""
    path = output_dir / "status.json"
    history: list[str] = []
    if path.is_file():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("persisted status evidence is malformed") from error
        if not isinstance(previous, Mapping):
            raise ValueError("persisted status evidence is malformed")
        previous_history = previous.get("history", [])
        if not isinstance(previous_history, list) or not all(
            isinstance(item, str) for item in previous_history
        ):
            raise ValueError("persisted status history is malformed")
        history = list(previous_history)
    if not history or history[-1] != stage:
        history.append(stage)
    _write_json(
        path,
        {"stage": stage, "history": history, "evidence": dict(evidence)},
    )


def _spoken_text(text: str, prepared: ContentPreparationResult) -> str:
    """Render a deterministic spoken transcript from source-bound token forms."""
    spoken = text
    tokens = sorted(
        prepared.tokens, key=lambda token: len(token.source_form), reverse=True
    )
    for token in tokens:
        spoken = spoken.replace(token.source_form, token.expected_spoken_form)
    return " ".join(spoken.split())


def _mcp_preview(source: str, profile_id: str) -> Mapping[str, object]:
    """Exercise the authenticated, side-effect-free MCP preview boundary."""
    server = AgentMCPServer(
        LocalGateway(), AuthenticatedContext("reference-demo-tenant")
    )
    return server.call("poddown_preview", {"source": source, "profile": profile_id})


def _validate_resume_evidence(
    root: Path,
    value: Mapping[str, object],
    prepared: ContentPreparationResult,
    fixture: _ReferenceFixture,
    selected: tuple[RenderOutcome, ...],
) -> None:
    """Authenticate package, publication, and result evidence before replay."""
    manifest_path = root / "packages" / f"{_EPISODE_VERSION_ID}.json"
    expected_manifest_sha = value.get("package_manifest_sha256")
    if (
        not manifest_path.is_file()
        or not isinstance(expected_manifest_sha, str)
        or hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        != expected_manifest_sha
    ):
        raise ValueError("persisted package evidence is unavailable or invalid")

    artifact_store = PackageArtifactStore(root / "package-artifacts")
    package = EpisodePackageService(artifact_store, root / "packages").get(
        _EPISODE_VERSION_ID
    )
    if package is None or {reference.name for reference in package.files} != set(
        REQUIRED_PACKAGE_ARTIFACTS
    ):
        raise ValueError("persisted package evidence is unavailable or invalid")
    try:
        for reference in package.files:
            artifact_store.read(reference)
    except (OSError, ValueError, RuntimeError) as error:
        raise ValueError(
            "persisted package evidence is unavailable or invalid"
        ) from error

    details = package.provenance.details
    workflow = details.get("workflow")
    if not isinstance(workflow, Mapping):
        raise ValueError("persisted result evidence is unavailable or invalid")
    voice_bindings = {
        segment.segment_id: next(
            speaker.voice_asset_id
            for speaker in prepared.profile.speakers
            if speaker.speaker_id == segment.speaker_ids[0]
        )
        for segment in prepared.segments
    }
    expected_workflow = {
        "workflow_id": "reference-demo-local-v1",
        "failed_segment_ids": workflow.get("failed_segment_ids"),
        "regenerated_segment_ids": workflow.get("regenerated_segment_ids"),
        "segment_ids": [segment.segment_id for segment in prepared.segments],
        "selected_candidate_ids": [
            outcome.candidate.candidate_id for outcome in selected
        ],
        "take_count": 3,
        "render_requests": len(selected) * 3,
        "voice_bindings": voice_bindings,
    }
    if any(
        workflow.get(key) != expected
        for key, expected in expected_workflow.items()
        if key not in {"failed_segment_ids", "regenerated_segment_ids"}
    ):
        raise ValueError("persisted result evidence is unavailable or invalid")
    for field in ("failed_segment_ids", "regenerated_segment_ids"):
        if not isinstance(workflow.get(field), list) or not all(
            isinstance(item, str) for item in cast(list[object], workflow[field])
        ):
            raise ValueError("persisted result evidence is unavailable or invalid")

    expected_result = {
        "mode": "deterministic-local-demo",
        "source_sha256": prepared.snapshot.source_sha256,
        "profile_id": prepared.profile.profile_id,
        "target_minutes": prepared.profile.target_minutes,
        "speakers": [speaker.speaker_id for speaker in prepared.profile.speakers],
        "segment_ids": expected_workflow["segment_ids"],
        "take_count": expected_workflow["take_count"],
        "selected_candidate_ids": expected_workflow["selected_candidate_ids"],
        "failed_segment_ids": workflow["failed_segment_ids"],
        "regenerated_segment_ids": workflow["regenerated_segment_ids"],
        "critical_token_accuracy": package.provenance.critical_token_accuracy,
        "package_artifacts": list(REQUIRED_PACKAGE_ARTIFACTS),
        "package_manifest_sha256": expected_manifest_sha,
        "usage": {"render_requests": expected_workflow["render_requests"]},
        "cost": details.get("cost"),
        "mcp_preview": _mcp_preview(fixture.source, prepared.profile.profile_id),
        "voice_bindings": expected_workflow["voice_bindings"],
    }
    if any(value.get(field) != expected for field, expected in expected_result.items()):
        raise ValueError("persisted result evidence is unavailable or invalid")

    publication_path = root / "publication.json"
    published_root = (
        root
        / "published"
        / "tenants"
        / str(_TENANT_ID)
        / "projects"
        / str(_PROJECT_ID)
        / "reference-demo"
    )
    try:
        publication = json.loads(publication_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(
            "persisted publication evidence is unavailable or invalid"
        ) from error
    if not isinstance(publication, Mapping) or (
        publication.get("external_id") != value.get("publication_path")
        or publication.get("episode_version_id") != str(_EPISODE_VERSION_ID)
        or publication.get("package_sha256") != package.provenance.final_sha256
        or publication.get("status") not in {"published", "resumed"}
        or not published_root.is_dir()
    ):
        raise ValueError("persisted publication evidence is unavailable or invalid")
    published_names = {path.name for path in published_root.iterdir() if path.is_file()}
    if published_names != set(REQUIRED_PACKAGE_ARTIFACTS):
        raise ValueError("persisted publication evidence is unavailable or invalid")
    for reference in package.files:
        data = (published_root / reference.name).read_bytes()
        if (
            len(data) != reference.byte_count
            or hashlib.sha256(data).hexdigest() != reference.sha256
        ):
            raise ValueError("persisted publication evidence is unavailable or invalid")


def _result_from_dict(value: Mapping[str, object], replayed_takes: int) -> DemoResult:
    """Rehydrate completed immutable evidence while recording render replay."""

    def string_tuple(field: str) -> tuple[str, ...]:
        raw = value.get(field)
        if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
            raise ValueError(f"persisted result field is malformed: {field}")
        return tuple(raw)

    def integer(field: str) -> int:
        raw = value.get(field)
        if type(raw) is not int:
            raise ValueError(f"persisted result field is malformed: {field}")
        return raw

    def number(field: str) -> float:
        raw = value.get(field)
        if not isinstance(raw, (int, float)) or isinstance(raw, bool):
            raise ValueError(f"persisted result field is malformed: {field}")
        return float(raw)

    def string_mapping(field: str) -> Mapping[str, str]:
        raw = value.get(field)
        if not isinstance(raw, Mapping) or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in raw.items()
        ):
            raise ValueError(f"persisted result field is malformed: {field}")
        return {str(key): str(item) for key, item in raw.items()}

    def integer_mapping(field: str) -> Mapping[str, int]:
        raw = value.get(field)
        if not isinstance(raw, Mapping) or not all(
            isinstance(key, str) and type(item) is int for key, item in raw.items()
        ):
            raise ValueError(f"persisted result field is malformed: {field}")
        return {str(key): int(item) for key, item in raw.items()}

    mcp_preview = value.get("mcp_preview")
    if not isinstance(mcp_preview, Mapping):
        raise ValueError("persisted result field is malformed: mcp_preview")
    return DemoResult(
        mode=str(value["mode"]),
        source_sha256=str(value["source_sha256"]),
        profile_id=str(value["profile_id"]),
        target_minutes=integer("target_minutes"),
        speakers=string_tuple("speakers"),
        segment_ids=string_tuple("segment_ids"),
        take_count=integer("take_count"),
        selected_candidate_ids=string_tuple("selected_candidate_ids"),
        failed_segment_ids=string_tuple("failed_segment_ids"),
        regenerated_segment_ids=string_tuple("regenerated_segment_ids"),
        critical_token_accuracy=number("critical_token_accuracy"),
        package_artifacts=string_tuple("package_artifacts"),
        package_manifest_sha256=str(value["package_manifest_sha256"]),
        publication_path=str(value["publication_path"]),
        usage=integer_mapping("usage"),
        cost=str(value["cost"]),
        replayed_takes=replayed_takes,
        mcp_preview=dict(mcp_preview),
        voice_bindings=string_mapping("voice_bindings"),
    )


async def _render_segments(
    prepared: ContentPreparationResult, fixture: _ReferenceFixture, output_dir: Path
) -> tuple[tuple[RenderOutcome, ...], tuple[str, ...], tuple[str, ...], int]:
    artifacts = FilesystemArtifactStore(output_dir / "artifacts")
    records = FilesystemRenderRecordStore(output_dir / "render-records", artifacts)
    service = DurableRenderService(artifacts, records)
    first_segment_id = prepared.segments[0].segment_id
    renderer = _FailOnceRenderer(first_segment_id)
    raw_assets = fixture.voices.get("assets")
    if not isinstance(raw_assets, list) or not all(
        isinstance(item, Mapping) for item in raw_assets
    ):
        raise ValueError("voice assets are malformed")
    voice_rows = {
        str(item["asset_id"]): item
        for item in cast(list[Mapping[str, object]], raw_assets)
    }
    selected: list[RenderOutcome] = []
    failed: list[str] = []
    regenerated: list[str] = []
    replayed = 0
    for segment in prepared.segments:
        speaker = next(
            item
            for item in prepared.profile.speakers
            if item.speaker_id == segment.speaker_ids[0]
        )
        request = RenderRequest(
            "reference-demo-episode-v1",
            "v1",
            segment.segment_id,
            speaker.speaker_id,
            segment.text,
            speaker.voice_asset_id,
            "local",
            "local-deterministic-v1",
        )
        row = voice_rows[request.voice_asset_id]
        consent = AudioVoiceConsent(
            request.voice_asset_id, str(row["consent_id"]), frozenset({"local"})
        )
        try:
            outcomes = await service.render_takes(
                request, consent, renderer, take_count=3
            )
        except RenderRejectedError:
            failed.append(segment.segment_id)
            regenerated.append(segment.segment_id)
            outcomes = await service.render_takes(
                request, consent, DeterministicLocalRenderer(), take_count=3
            )
        replayed += sum(outcome.replayed for outcome in outcomes)
        segment_transcript = " ".join(
            token.expected_spoken_form for token in segment.critical_tokens
        )
        qualities = tuple(
            CandidateQuality(
                outcome.candidate.candidate_id,
                evaluate_critical_tokens(
                    tuple(
                        token.expected_spoken_form for token in segment.critical_tokens
                    ),
                    segment_transcript,
                ),
                diagnose_wav(artifacts.read(outcome.candidate.artifact)),
                True,
                Decimal("1"),
            )
            for outcome in outcomes
        )
        choice = select_candidate(qualities)
        if choice is None:
            raise ValueError("deterministic local candidates failed hard gates")
        selected.append(
            next(
                outcome
                for outcome in outcomes
                if outcome.candidate.candidate_id == choice.candidate_id
            )
        )
    return tuple(selected), tuple(failed), tuple(regenerated), replayed


def run_reference_demo(
    output_dir: Path,
    *,
    resume: bool = False,
    mastering_runner: FfmpegRunner | None = None,
) -> DemoResult:
    """Run or safely replay the complete deterministic-local reference episode."""
    root = Path(output_dir)
    if root.exists() and not root.is_dir():
        raise ValueError("reference demo output must be a directory")
    root.mkdir(parents=True, exist_ok=True)
    fixture = _load_reference_fixture(_reference_fixture_root())
    prepared = prepare_content(_build_content_request(fixture))
    if not isinstance(prepared, ContentPreparationResult):
        raise ValueError("content preparation did not return typed evidence")
    _write_status(root, "ingested", {"source_sha256": prepared.snapshot.source_sha256})
    _write_status(root, "prepared", {"manifest_sha256": prepared.manifest_sha256})
    selected, failed, regenerated, replayed = asyncio.run(
        _render_segments(prepared, fixture, root)
    )
    _write_status(
        root,
        "rendering",
        {"failed_segment_ids": list(failed), "replayed_takes": replayed},
    )
    if resume and (root / "result.json").is_file():
        previous = json.loads((root / "result.json").read_text(encoding="utf-8"))
        if not isinstance(previous, Mapping):
            raise ValueError("persisted result evidence is malformed")
        _validate_resume_evidence(root, previous, prepared, fixture, selected)
        result = _result_from_dict(previous, replayed)
        _write_json(root / "result.json", result.to_dict())
        _write_status(root, "completed", result.to_dict())
        return result
    profile = MasteringProfile(silence_ms=0, max_peak_amplitude=1.0)
    master = MasteringService(mastering_runner).master(
        MasteringRequest(
            "reference-demo-episode-v1",
            "v1",
            tuple(
                MasteringSegment(
                    index,
                    outcome.candidate.segment_id,
                    FilesystemArtifactStore(root / "artifacts").read(
                        outcome.candidate.artifact
                    ),
                )
                for index, outcome in enumerate(selected)
            ),
            profile,
        )
    )
    transcript_text = " ".join(
        _spoken_text(turn.text, prepared) for turn in prepared.script.turns
    )
    qa = asyncio.run(
        FinalMasterQaService(_DeterministicTranscriber(transcript_text)).evaluate(
            master,
            profile,
            tuple(token.expected_spoken_form for token in prepared.tokens),
        )
    )
    _write_status(root, "qa", qa.to_dict())
    _write_status(root, "mastering", {"wav_checksum": master.provenance.wav_checksum})
    disclosure_text = cast(str, fixture.disclosure["text"]).strip()
    content_manifest = dict(prepared.manifest)
    content_manifest["show_notes"] = (
        disclosure_text,
        "On 2026-07-31, the LiDAR team measured a C1 calibration result of "
        "99.7% across the 1.2 km test corridor.",
    )
    voice_bindings = {
        segment.segment_id: next(
            speaker.voice_asset_id
            for speaker in prepared.profile.speakers
            if speaker.speaker_id == segment.speaker_ids[0]
        )
        for segment in prepared.segments
    }
    render_evidence = {
        "workflow_id": "reference-demo-local-v1",
        "failed_segment_ids": list(failed),
        "regenerated_segment_ids": list(regenerated),
        "segment_ids": [segment.segment_id for segment in prepared.segments],
        "selected_candidate_ids": [
            outcome.candidate.candidate_id for outcome in selected
        ],
        "take_count": 3,
        "render_requests": len(selected) * 3,
        "voice_bindings": voice_bindings,
    }
    package_input = PackageGenerationInput(
        _EPISODE_VERSION_ID,
        prepared.snapshot,
        prepared.profile,
        prepared.script,
        1,
        prepared.segments,
        content_manifest,
        master,
        qa,
        render_evidence,
        "local-deterministic-v1",
        {"fixture": "reference-demo/v1"},
    )
    artifacts = generate_package_artifacts(package_input)
    package = EpisodePackageService(
        PackageArtifactStore(root / "package-artifacts"), root / "packages"
    ).commit(
        _EPISODE_VERSION_ID,
        artifacts,
        __import__(
            "poddown.package_generation", fromlist=["build_package_provenance"]
        ).build_package_provenance(package_input),
    )
    manifest_path = root / "packages" / f"{_EPISODE_VERSION_ID}.json"
    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    _write_status(root, "packaged", {"package_manifest_sha256": manifest_sha})
    ledger = SQLiteUsageLedger(root / "usage.sqlite")
    for outcome in selected:
        ledger.record(
            UsageEvent(
                _TENANT_ID,
                _PROJECT_ID,
                _EPISODE_VERSION_ID,
                outcome.candidate.request_id,
                "render",
                {"requests": 3},
                "USD",
                Decimal("0"),
                Decimal("0"),
                datetime(2026, 8, 10, tzinfo=UTC),
            )
        )
    target = PublicationTarget(
        _TENANT_ID,
        _PROJECT_ID,
        "reference-demo",
        "filesystem",
        "secret://deterministic-local",
        "reference-demo",
        "https://example.invalid/reference-demo.xml",
        DisclosurePolicy(
            cast(bool, fixture.disclosure["spoken"]),
            cast(bool, fixture.disclosure["show_notes"]),
            cast(bool, fixture.disclosure["platform"]),
        ),
    )
    receipt = PublishingService(
        artifact_store=PackageArtifactStore(root / "package-artifacts"),
        adapters={"filesystem": FilesystemPublicationAdapter(root / "published")},
    ).publish(
        package,
        target,
        PublicationAuthorization(
            "reference-demo", "local-publication-v1", "deterministic local demo"
        ),
        "reference-demo-publication-v1",
    )
    _write_json(
        root / "usage.json", {"render_requests": len(selected) * 3, "cost": "0"}
    )
    _write_json(root / "publication.json", receipt.to_dict())
    _write_status(root, "published", {"publication_id": receipt.external_id})
    result = DemoResult(
        "deterministic-local-demo",
        prepared.snapshot.source_sha256,
        prepared.profile.profile_id,
        prepared.profile.target_minutes,
        tuple(speaker.speaker_id for speaker in prepared.profile.speakers),
        tuple(segment.segment_id for segment in prepared.segments),
        3,
        tuple(outcome.candidate.candidate_id for outcome in selected),
        failed,
        regenerated,
        qa.critical_token_accuracy,
        tuple(artifact.name for artifact in artifacts),
        manifest_sha,
        receipt.external_id,
        {"render_requests": len(selected) * 3},
        "0",
        replayed if resume else 0,
        _mcp_preview(fixture.source, prepared.profile.profile_id),
        voice_bindings,
    )
    _write_json(root / "result.json", result.to_dict())
    _write_status(root, "completed", result.to_dict())
    return result


def main(argv: Sequence[str] | None = None) -> int:
    """Run the deterministic-local demo command without accepting live mode."""
    parser = argparse.ArgumentParser(prog="poddown-demo")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    print(
        json.dumps(
            run_reference_demo(args.output, resume=args.resume).to_dict(),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
