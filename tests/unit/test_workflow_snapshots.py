"""Unit contracts for immutable API-to-Temporal workflow snapshots."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

import pytest

from poddown.audio.contracts import RenderRequest
from poddown.audio.production_workflow import ProductionWorkflowInput
from poddown.audio.rights import VoiceConsent
from poddown.audio.workflow import EpisodeWorkflowInput, SegmentWorkflowInput
from poddown.content.adaptation import (
    AdaptationProposal,
    EpisodeTreatment,
    FixtureReasoningPort,
)
from poddown.content.lexicon import PronunciationEntry, PronunciationLexicon
from poddown.content.models import (
    ScriptTurn,
    SourceAnchor,
    VoiceAsset,
)
from poddown.content.models import (
    VoiceConsent as ContentVoiceConsent,
)
from poddown.content.segmentation import SegmentationCapabilities
from poddown.content.service import ContentPreparationRequest, _build_result
from poddown.content.source import snapshot_source
from poddown.episode_service import EpisodeRecord, EpisodeState
from poddown.workflow_snapshots import (
    EpisodeWorkflowSnapshot,
    WorkflowRenderBinding,
    WorkflowSnapshotError,
    bind_snapshot_to_record,
    build_render_workflow_input,
    build_workflow_snapshot,
)

TENANT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")
EPISODE = UUID("01986e76-4ec6-7a8f-8000-000000000001")
SOURCE = b"source snapshot"


def _record() -> EpisodeRecord:
    digest = sha256(SOURCE).hexdigest()
    return EpisodeRecord(
        tenant_id=TENANT,
        project_id=PROJECT,
        episode_id=EPISODE,
        idempotency_key="create-1",
        profile_name="technical-dialogue",
        source_sha256=digest,
        source_bytes=len(SOURCE),
        source_content=SOURCE,
        request_fingerprint="a" * 64,
        state=EpisodeState.VALIDATED,
        version=1,
        created_at=datetime(2026, 8, 14, tzinfo=UTC),
        updated_at=datetime(2026, 8, 14, tzinfo=UTC),
    )


def _snapshot(
    *,
    episode_id: str = str(EPISODE),
    version: str = "v1",
    render_episode_id: str | None = None,
):
    request = RenderRequest(
        episode_id=render_episode_id or episode_id,
        episode_version=version,
        segment_id="segment-1",
        speaker_id="host",
        expected_spoken_text="A source-bound segment.",
        voice_asset_id="voice-host-v1",
        provider="local",
        model="local-deterministic-v1",
    )
    segment = SegmentWorkflowInput(
        segment_id="segment-1",
        render_request=request,
        consent=VoiceConsent("voice-host-v1", "consent-1", frozenset({"local"})),
        critical_tokens=("source-bound",),
    )
    return EpisodeWorkflowSnapshot(
        source_sha256=_record().source_sha256,
        workflow_input=EpisodeWorkflowInput(episode_id, version, (segment,)),
    )


def test_binding_requires_source_episode_and_version_identity() -> None:
    snapshot = _snapshot()
    bound = bind_snapshot_to_record(snapshot, _record())

    assert bound is snapshot
    assert bound.to_payload()["source_sha256"] == _record().source_sha256
    assert isinstance(bound.to_payload()["workflow_input"], str)

    with pytest.raises(WorkflowSnapshotError, match="version"):
        bind_snapshot_to_record(_snapshot(version="2"), _record())
    with pytest.raises(WorkflowSnapshotError, match="episode"):
        bind_snapshot_to_record(_snapshot(episode_id="other"), _record())


def test_snapshot_rejects_inconsistent_render_identity() -> None:
    with pytest.raises(WorkflowSnapshotError, match="identity"):
        _snapshot(render_episode_id="other")


def test_source_only_production_snapshot_omits_fabricated_render_input() -> None:
    production = ProductionWorkflowInput(
        episode_id=str(EPISODE),
        episode_version_id=str(EPISODE),
        source_sha256=_record().source_sha256,
        profile_id="technical-dialogue",
        prepared_content_reference={
            "preparation_activity_input": {
                "tenant_id": str(TENANT),
                "project_id": str(PROJECT),
                "episode_id": str(EPISODE),
                "episode_version_id": str(EPISODE),
                "source_markdown": SOURCE.decode(),
                "profile_id": "technical-dialogue",
                "execution_mode": "live-provider",
                "max_attempts": 2,
            }
        },
        execution_mode="live-provider",
    )
    snapshot = EpisodeWorkflowSnapshot(
        source_sha256=_record().source_sha256,
        workflow_input=None,
        production_input_json=production.to_json(),
    )

    payload = snapshot.to_payload()
    assert "workflow_input" not in payload
    assert payload["production_workflow_input"] == production.to_json()
    assert bind_snapshot_to_record(snapshot, _record()) is snapshot


def test_builder_preserves_prepared_turns_pronunciation_and_consent() -> None:
    markdown = "---\npoddown:\n  profile: narration\n---\nLiDAR\n"
    source = snapshot_source(markdown)
    block = source.blocks[0]
    anchor = SourceAnchor(block.block_id, block.start, block.end)
    treatment = EpisodeTreatment(
        "lidar-episode",
        "narration",
        ("evidence",),
        1,
        ("intro",),
        {"host": "host"},
        (anchor,),
        ("turn-1",),
    )
    proposal = AdaptationProposal(
        treatment,
        (ScriptTurn("turn-1", "host", "LiDAR", "factual", (anchor,), (anchor,)),),
    )
    result = _build_result(
        ContentPreparationRequest(
            markdown,
            (
                "profile_id: narration\nversion: v1\nformat_type: narration\n"
                "target_minutes: 1\nspeakers:\n  - speaker_id: host\n"
                "    display_name: Host\n    voice_asset_id: voice-host\n"
            ),
            treatment,
            FixtureReasoningPort({source.source_sha256: proposal}, {}),
            {
                "project": PronunciationLexicon(
                    "project",
                    "v1",
                    (
                        PronunciationEntry(
                            "lidar",
                            "LiDAR",
                            "LIE-dar",
                            "v1",
                            category="technical_term",
                        ),
                    ),
                )
            },
            SegmentationCapabilities(100, None, frozenset({"host"})),
            (VoiceAsset("voice-host", True),),
            (ContentVoiceConsent("voice-host", True),),
        )
    )
    record = replace(
        _record(),
        profile_name="narration",
        source_content=markdown.encode(),
        source_bytes=len(markdown.encode()),
        source_sha256=source.source_sha256,
    )

    binding = WorkflowRenderBinding(
        "local",
        "local-deterministic-v1",
        {"voice-host": VoiceConsent("voice-host", "consent-1", frozenset({"local"}))},
    )
    render_input = build_render_workflow_input(
        result,
        episode_id=str(record.episode_id),
        episode_version=f"v{record.version}",
        binding=binding,
    )
    snapshot = build_workflow_snapshot(result, record, binding)

    assert render_input.to_json() == snapshot.workflow_input.to_json()
    assert render_input.segments[0].render_request.expected_spoken_text == "LIE-dar"
    assert render_input.segments[0].critical_tokens == ("LIE-dar",)
