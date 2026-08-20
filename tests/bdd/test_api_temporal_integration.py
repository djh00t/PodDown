"""BDD acceptance for the real API-to-Temporal command wire boundary."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from pytest_bdd import given, scenarios, then, when
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from uuid6 import uuid7

from poddown.api import create_app
from poddown.api.temporal_dispatcher import TemporalClientTransport
from poddown.audio.activities import (
    build_durable_render_activity,
    deterministic_quality_evaluator,
)
from poddown.audio.local import DeterministicLocalRenderer
from poddown.audio.render import DurableRenderService
from poddown.audio.rights import VoiceConsent
from poddown.audio.storage import (
    FilesystemArtifactStore,
    FilesystemRenderRecordStore,
)
from poddown.audio.workflow import (
    EpisodeCommandWorkflow,
    EpisodeRenderWorkflow,
    EpisodeWorkflowInput,
    RenderRequest,
    SegmentWorkflowInput,
)
from poddown.workflow_snapshots import EpisodeWorkflowSnapshot
from tests.temporal_support import retry_local_temporal_environment

scenarios("../features/api_temporal_integration.feature")

TENANT = "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10"
PROJECT = "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12"
PROFILE = "technical-dialogue"
SOURCE = "---\npoddown:\n  profile: technical-dialogue\n---\n# Temporal API fixture\n"


def _snapshot(episode_id: str, source_sha256: str) -> EpisodeWorkflowSnapshot:
    """Build one small deterministic snapshot for the wire test."""
    segment_id = "segment-api-temporal-001"
    segment = SegmentWorkflowInput(
        segment_id=segment_id,
        render_request=RenderRequest(
            episode_id=episode_id,
            episode_version="v1",
            segment_id=segment_id,
            speaker_id="host",
            expected_spoken_text="A source-bound Temporal command.",
            voice_asset_id="voice-host-v1",
            provider="local",
            model="local-deterministic-v1",
        ),
        consent=VoiceConsent(
            "voice-host-v1", "consent-api-temporal-001", frozenset({"local"})
        ),
        critical_tokens=("source-bound", "Temporal"),
    )
    return EpisodeWorkflowSnapshot(
        source_sha256=source_sha256,
        workflow_input=EpisodeWorkflowInput(
            episode_id=episode_id,
            episode_version="v1",
            segments=(segment,),
            tenant_id=TENANT,
            project_id=PROJECT,
            max_attempts=1,
        ),
    )


@given("a source-bound deterministic render fixture")
def source_bound_fixture(context: dict[str, Any]) -> None:
    """Store the immutable fixture inputs used by the API factory."""

    class Factory:
        def build(
            self, *, record: Any, command: str, payload: Any
        ) -> EpisodeWorkflowSnapshot:
            del command, payload
            return _snapshot(str(record.episode_id), record.source_sha256)

    context.values["snapshot_factory"] = Factory()


@when("the API command runs against a local Temporal worker")
def run_api_command(context: dict[str, Any], tmp_path: Path) -> None:
    """Compose the API and worker against one real local Temporal server."""

    async def run() -> dict[str, Any]:
        async with retry_local_temporal_environment(
            WorkflowEnvironment.start_local
        ) as environment:
            task_queue = f"poddown-api-temporal-{uuid7().hex}"
            artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
            records = FilesystemRenderRecordStore(
                tmp_path / "render-records", artifacts
            )
            render_activity = build_durable_render_activity(
                DurableRenderService(artifacts, records),
                DeterministicLocalRenderer(),
                artifacts,
                quality_evaluator=deterministic_quality_evaluator,
            )
            temporal_address = environment.client.service_client.config.target_host

            async def client_factory(address: str, *, namespace: str) -> Client:
                return await Client.connect(address, namespace=namespace)

            transport = TemporalClientTransport(
                temporal_address,
                client_factory=client_factory,
            )
            app = create_app(
                temporal_transport=transport,
                temporal_task_queue=task_queue,
                workflow_snapshot_factory=context.values["snapshot_factory"],
            )
            async with Worker(
                environment.client,
                task_queue=task_queue,
                workflows=[EpisodeCommandWorkflow, EpisodeRenderWorkflow],
                activities=[render_activity],
            ):
                with TestClient(app) as client:
                    response = client.post(
                        "/v1/episodes",
                        headers={
                            "X-Tenant-ID": TENANT,
                            "X-Project-ID": PROJECT,
                            "Idempotency-Key": "temporal-001",
                        },
                        json={"source": SOURCE, "profile": PROFILE},
                    )
                body = response.json()
                workflow_id = body["receipt"]["workflow_id"]
                result = await environment.client.get_workflow_handle(
                    workflow_id
                ).result()
                return {
                    "response_status": response.status_code,
                    "body": body,
                    "result": json.loads(result),
                }

    context.values["execution"] = asyncio.run(run())


@then("the dispatched Temporal workflow completes the render snapshot")
def temporal_workflow_completes(context: dict[str, Any]) -> None:
    """Require API acceptance and a completed result from the real worker."""
    execution = context.values["execution"]
    assert execution["response_status"] == 202
    assert execution["body"]["receipt"]["state"] == "dispatched"
    assert execution["body"]["receipt"]["workflow_id"]
    assert execution["result"]["status"] == "completed"
    assert len(execution["result"]["decisions"]) == 1
    assert execution["result"]["decisions"][0]["accepted_candidate_id"]
