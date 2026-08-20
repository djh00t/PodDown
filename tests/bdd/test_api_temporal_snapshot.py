"""BDD coverage for source-bound API-to-Temporal command composition."""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient
from pytest_bdd import given, scenarios, then, when
from uuid6 import uuid7

from poddown.api import create_app
from poddown.api.temporal_dispatcher import TemporalCommandRequest
from poddown.audio.contracts import RenderRequest
from poddown.audio.rights import VoiceConsent
from poddown.audio.workflow import EpisodeWorkflowInput, SegmentWorkflowInput
from poddown.workflow_snapshots import EpisodeWorkflowSnapshot

scenarios("../features/api_temporal_snapshot.feature")

TENANT = "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10"
PROJECT = "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12"
SOURCE = "---\npoddown:\n  profile: technical-dialogue\n---\n# Snapshot\n"


class _Transport:
    def __init__(self) -> None:
        self.requests: list[TemporalCommandRequest] = []

    def start_workflow(self, request: TemporalCommandRequest) -> None:
        self.requests.append(request)


def _snapshot(episode_id: str, source_sha256: str) -> EpisodeWorkflowSnapshot:
    segment_id = "segment-snapshot-001"
    request = RenderRequest(
        episode_id=episode_id,
        episode_version="v1",
        segment_id=segment_id,
        speaker_id="host",
        expected_spoken_text="A source-bound snapshot.",
        voice_asset_id="voice-host-v1",
        provider="local",
        model="local-deterministic-v1",
    )
    segment = SegmentWorkflowInput(
        segment_id=segment_id,
        render_request=request,
        consent=VoiceConsent(
            "voice-host-v1", "consent-snapshot-001", frozenset({"local"})
        ),
        critical_tokens=("source-bound",),
    )
    return EpisodeWorkflowSnapshot(
        source_sha256=source_sha256,
        workflow_input=EpisodeWorkflowInput(
            episode_id=episode_id,
            episode_version="v1",
            segments=(segment,),
        ),
    )


@given("an API with a source-bound workflow snapshot factory")
def api_with_snapshot_factory(context: dict[str, Any]) -> None:
    transport = _Transport()

    class Factory:
        def build(self, *, record, command, payload):
            del command, payload
            return _snapshot(str(record.episode_id), record.source_sha256)

    context.values["client"] = TestClient(
        create_app(
            temporal_transport=transport,
            temporal_task_queue="poddown-default",
            workflow_snapshot_factory=Factory(),
        )
    )
    context.values["transport"] = transport


@given("an API with a mismatched workflow snapshot factory")
def api_with_mismatched_snapshot_factory(context: dict[str, Any]) -> None:
    transport = _Transport()

    class Factory:
        def build(self, *, record, command, payload):
            del command, payload
            return _snapshot(str(record.episode_id), "0" * 64)

    context.values["client"] = TestClient(
        create_app(
            temporal_transport=transport,
            temporal_task_queue="poddown-default",
            workflow_snapshot_factory=Factory(),
        )
    )
    context.values["transport"] = transport


@when("the API creates an episode for Temporal dispatch")
def create_for_temporal(context: dict[str, Any]) -> None:
    context.values["response"] = context.values["client"].post(
        "/v1/episodes",
        headers={
            "X-Tenant-ID": TENANT,
            "X-Project-ID": PROJECT,
            "Idempotency-Key": f"snapshot-{uuid7()}",
        },
        json={"source": SOURCE, "profile": "technical-dialogue"},
    )


@then("the Temporal payload contains the matching source snapshot")
def temporal_payload_contains_snapshot(context: dict[str, Any]) -> None:
    response = context.values["response"]
    assert response.status_code == 202
    request = context.values["transport"].requests[0]
    envelope = json.loads(request.payload)
    command_payload = envelope["payload"]
    assert (
        command_payload["source_sha256"] == response.json()["episode"]["source_sha256"]
    )
    assert isinstance(command_payload["workflow_input"], str)
    assert (
        json.loads(command_payload["workflow_input"])["episode_id"]
        == response.json()["episode"]["id"]
    )


@then("the API reports an unavailable workflow snapshot")
def unavailable_snapshot(context: dict[str, Any]) -> None:
    response = context.values["response"]
    assert response.status_code == 503
    assert response.json()["code"] == "workflow_snapshot_unavailable"
    assert context.values["transport"].requests == []
