"""BDD coverage for explicit runtime workflow snapshot composition."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pytest_bdd import given, scenarios, then, when
from uuid6 import uuid7

from poddown.episode_service import EpisodeRecord, EpisodeState
from poddown.runtime_snapshots import ReferenceFixtureWorkflowSnapshotFactory
from poddown.workflow_snapshots import WorkflowSnapshotError

scenarios("../features/runtime_snapshot_factory.feature")

FIXTURE = Path("integrations/reference-demo/v1")
TENANT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")


def _record(*, source: bytes | None = None) -> EpisodeRecord:
    content = (FIXTURE / "source.md").read_bytes() if source is None else source
    return EpisodeRecord(
        tenant_id=TENANT,
        project_id=PROJECT,
        episode_id=uuid7(),
        idempotency_key="runtime-snapshot-test",
        profile_name="reference-demo-dialogue-v1",
        source_sha256=sha256(content).hexdigest(),
        source_bytes=len(content),
        source_content=content,
        request_fingerprint="a" * 64,
        state=EpisodeState.VALIDATED,
        version=1,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@given("an explicit reference workflow snapshot factory")
def explicit_factory(context: dict[str, Any]) -> None:
    context.values["factory"] = ReferenceFixtureWorkflowSnapshotFactory(
        FIXTURE, mode="host-local"
    )
    context.values["record"] = _record()


@given("an explicit deterministic reference workflow snapshot factory")
def explicit_deterministic_factory(context: dict[str, Any]) -> None:
    context.values["factory"] = ReferenceFixtureWorkflowSnapshotFactory(
        FIXTURE, mode="deterministic-local"
    )
    context.values["record"] = _record()


@when("I build a create workflow snapshot for the reference episode")
def build_reference_snapshot(context: dict[str, Any]) -> None:
    context.values["snapshot"] = context.values["factory"].build(
        record=context.values["record"], command="create", payload=None
    )


@then("the snapshot preserves the source hash and configured host-local binding")
def assert_reference_snapshot(context: dict[str, Any]) -> None:
    snapshot = context.values["snapshot"]
    record = context.values["record"]
    assert snapshot.source_sha256 == record.source_sha256
    assert snapshot.workflow_input.episode_id == str(record.episode_id)
    assert all(
        segment.render_request.provider == "host-local"
        and segment.render_request.model == "host-local-tts-v1"
        and "host-local" in segment.consent.allowed_providers
        for segment in snapshot.workflow_input.segments
    )


@then("each snapshot segment carries only its source turn critical tokens")
def assert_source_turn_critical_tokens(context: dict[str, Any]) -> None:
    segments = context.values["snapshot"].workflow_input.segments
    assert segments[0].critical_tokens == ()
    assert segments[1].critical_tokens == ("LIE-dar", "see one")
    assert segments[2].critical_tokens == ()


@when("I build a snapshot for a different source")
def build_different_source(context: dict[str, Any]) -> None:
    context.values["error"] = _capture_error(
        lambda: context.values["factory"].build(
            record=_record(source=b"different source"),
            command="create",
            payload=None,
        )
    )


@when("I build a host-local snapshot with a deterministic-local command mode")
def build_mismatched_mode(context: dict[str, Any]) -> None:
    context.values["error"] = _capture_error(
        lambda: context.values["factory"].build(
            record=context.values["record"],
            command="render",
            payload={"mode": "deterministic-local"},
        )
    )


@then("runtime snapshot composition fails closed")
def assert_runtime_snapshot_error(context: dict[str, Any]) -> None:
    assert isinstance(context.values["error"], WorkflowSnapshotError)


def _capture_error(operation):
    with pytest.raises(WorkflowSnapshotError) as captured:
        operation()
    return captured.value
