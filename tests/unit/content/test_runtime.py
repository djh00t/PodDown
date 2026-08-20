"""Contracts for configured source-only worker workflow snapshots."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest

from poddown.audio.prepare_activity import (
    PrepareContentActivityInput,
    build_prepare_content_activity,
)
from poddown.audio.production_workflow import ProductionWorkflowInput
from poddown.audio.rights import VoiceConsent as AudioVoiceConsent
from poddown.content.persistence import PreparedContentStore
from poddown.content.runtime import ConfiguredContentWorkflowSnapshotFactory
from poddown.episode_service import EpisodeRecord, EpisodeState
from poddown.workflow_snapshots import (
    WorkflowRenderBinding,
    WorkflowSnapshotError,
    build_render_workflow_input,
)

SOURCE = "LiDAR remains source-bound.\n"
TENANT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")
EPISODE = UUID("01986e76-4ec6-7a8f-8000-000000000001")


def _record() -> EpisodeRecord:
    return EpisodeRecord(
        tenant_id=TENANT,
        project_id=PROJECT,
        episode_id=EPISODE,
        idempotency_key="configured-content-test",
        profile_name="configured-narration",
        source_sha256=sha256(SOURCE.encode()).hexdigest(),
        source_bytes=len(SOURCE.encode()),
        source_content=SOURCE.encode(),
        request_fingerprint="a" * 64,
        state=EpisodeState.VALIDATED,
        version=1,
        created_at=datetime(2026, 8, 15, tzinfo=UTC),
        updated_at=datetime(2026, 8, 15, tzinfo=UTC),
    )


def _write_config(root: Path, *, adaptation: bool) -> None:
    root.mkdir(exist_ok=True)
    (root / "source.md").write_text(SOURCE, encoding="utf-8")
    (root / "profile.yaml").write_text(
        """profile_id: configured-narration
version: v1
format_type: narration
target_minutes: 1
speakers:
  - speaker_id: host
    display_name: Host
    voice_asset_id: voice-host
""",
        encoding="utf-8",
    )
    (root / "voices.yaml").write_text(
        """assets:
  - asset_id: voice-host
    consent_id: consent-host
    consent_status: approved
    allowed_providers: [local, host-local]
    local_voice:
      macos_say_name: Samantha
      espeak_name: en-us
""",
        encoding="utf-8",
    )
    (root / "treatment.yaml").write_text(
        """treatment_id: configured-treatment
format_type: narration
narrative_arc: [source-bound]
target_minutes: 1
sections: [source]
speaker_roles:
  host: presenter
""",
        encoding="utf-8",
    )
    if adaptation:
        (root / "adaptation.json").write_text(
            """{
  "proposal_id": "configured-treatment",
  "source_turns": [
    {"turn_id": "turn-1", "speaker_id": "host", "claim_anchor": "claim-1"}
  ],
  "claims": [
    {
      "claim_anchor": "claim-1",
      "source_value": "LiDAR remains source-bound.",
      "adapted_value": "LiDAR remains source-bound."
    }
  ]
}
""",
            encoding="utf-8",
        )


def test_live_configured_factory_emits_source_only_authority(tmp_path: Path) -> None:
    _write_config(tmp_path, adaptation=False)
    binding = WorkflowRenderBinding(
        "elevenlabs",
        "eleven-multilingual-v2",
        {
            "provider-voice": AudioVoiceConsent(
                "provider-voice", "consent-provider", frozenset({"elevenlabs"})
            )
        },
        voice_asset_ids={"voice-host": "provider-voice"},
    )
    factory = ConfiguredContentWorkflowSnapshotFactory(
        tmp_path,
        mode="live-provider",
        render_binding=binding,
    )

    snapshot = factory.build(record=_record(), command="create", payload=None)

    assert snapshot.workflow_input is None
    assert snapshot.production_input_json is not None
    production = ProductionWorkflowInput.from_json(snapshot.production_input_json)
    assert (
        "episode_render_workflow_input_json"
        not in production.prepared_content_reference
    )
    assert production.prepared_content_reference["prepared_content_key"]
    request = factory.preparation_request_for(SOURCE, "configured-narration")
    assert request.treatment.treatment_id == "configured-treatment"


def test_local_configured_factory_requires_source_bound_adaptation(
    tmp_path: Path,
) -> None:
    _write_config(tmp_path, adaptation=False)

    with pytest.raises(WorkflowSnapshotError, match="adaptation"):
        ConfiguredContentWorkflowSnapshotFactory(tmp_path, mode="host-local")


def test_local_configured_factory_accepts_source_bound_adaptation(
    tmp_path: Path,
) -> None:
    _write_config(tmp_path, adaptation=True)
    factory = ConfiguredContentWorkflowSnapshotFactory(tmp_path, mode="host-local")

    request = factory.preparation_request_for(SOURCE, "configured-narration")

    assert request.treatment.expected_turn_ids == ("turn-1",)
    assert request.reasoning is not None


def test_local_preparation_persists_the_worker_render_handoff(tmp_path: Path) -> None:
    _write_config(tmp_path, adaptation=True)
    factory = ConfiguredContentWorkflowSnapshotFactory(tmp_path, mode="host-local")
    record = _record()
    snapshot = factory.build(record=record, command="create", payload=None)
    assert snapshot.production_input_json is not None
    production = ProductionWorkflowInput.from_json(snapshot.production_input_json)
    preparation = production.prepared_content_reference["preparation_activity_input"]
    assert isinstance(preparation, Mapping)
    value = PrepareContentActivityInput(**preparation)
    store = PreparedContentStore(tmp_path / "worker-store")

    result = build_prepare_content_activity(
        lambda item: factory.preparation_request_for(
            item.source_markdown, item.profile_id
        ),
        lambda item, prepared: build_render_workflow_input(
            prepared,
            episode_id=item.episode_id,
            episode_version=item.episode_version_id or "",
            binding=factory.render_binding,
        ).to_json(),
        prepared_content_recorder=lambda item, prepared: store.save(
            item.prepared_content_key or "", prepared
        ),
    )(value)

    assert result.render_workflow_input_json
    assert (
        store.load(value.prepared_content_key or "").script.turns[0].turn_id == "turn-1"
    )
