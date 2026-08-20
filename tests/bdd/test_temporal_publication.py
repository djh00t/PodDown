"""BDD acceptance for the Temporal publication activity boundary."""

from __future__ import annotations

import asyncio
import json
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

from pytest_bdd import given, scenarios, then, when
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from poddown.artifacts import FilesystemArtifactStore
from poddown.audio import (
    EpisodeCommandWorkflow,
    EpisodeWorkflowInput,
    RenderRequest,
    SegmentWorkflowInput,
    VoiceConsent,
    build_durable_publication_activity,
    build_publication_request_resolver,
)
from poddown.packages import (
    REQUIRED_PACKAGE_ARTIFACTS,
    EpisodePackage,
    PackageProvenance,
    manifest_sha256_for,
    package_sha256_for,
)
from poddown.publishing import (
    DisclosurePolicy,
    FilesystemPublicationAdapter,
    PublicationTarget,
    PublishingService,
)
from tests.temporal_support import retry_local_temporal_environment

scenarios("../features/temporal_publication.feature")

TENANT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")
EPISODE = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11")


def _workflow_input() -> str:
    segment = SegmentWorkflowInput(
        segment_id="segment-1",
        render_request=RenderRequest(
            episode_id=str(EPISODE),
            episode_version="v1",
            segment_id="segment-1",
            speaker_id="host",
            expected_spoken_text="A verified publication.",
            voice_asset_id="voice-host-v1",
            provider="local",
            model="local-deterministic-v1",
        ),
        consent=VoiceConsent("voice-host-v1", "consent-1", frozenset({"local"})),
        critical_tokens=("verified",),
    )
    return EpisodeWorkflowInput(
        episode_id=str(EPISODE),
        episode_version="v1",
        segments=(segment,),
    ).to_json()


def _package(store: FilesystemArtifactStore) -> EpisodePackage:
    references = tuple(
        store.put(
            name,
            "audio/mpeg" if name.endswith((".mp3", ".wav")) else "text/plain",
            b"exact episode bytes" if name == "episode.wav" else name.encode(),
        )
        for name in REQUIRED_PACKAGE_ARTIFACTS
    )
    wav = next(reference for reference in references if reference.name == "episode.wav")
    return EpisodePackage(
        episode_version_id=str(EPISODE),
        files=references,
        provenance=PackageProvenance(
            source_sha256="a" * 64,
            script_version=1,
            profile_version="v1",
            renderer="deterministic-local",
            qa="pass",
            critical_token_accuracy=1.0,
            final_sha256=wav.sha256,
        ),
    )


def _target() -> PublicationTarget:
    return PublicationTarget(
        tenant_id=TENANT,
        project_id=PROJECT,
        target_id="filesystem-target",
        kind="filesystem",
        secret_ref="secret://publication/filesystem",
        show_id="show-1",
        feed_url="https://example.test/feed.xml",
        disclosure=DisclosurePolicy(),
    )


def _command(
    package: EpisodePackage,
    target: PublicationTarget,
    store: FilesystemArtifactStore,
) -> str:
    return json.dumps(
        {
            "tenant_id": str(TENANT),
            "project_id": str(PROJECT),
            "episode_id": str(EPISODE),
            "command": "publish",
            "payload": {
                "source_sha256": "a" * 64,
                "workflow_input": _workflow_input(),
                "tenant_id": str(TENANT),
                "project_id": str(PROJECT),
                "episode_id": str(EPISODE),
                "idempotency_key": "fixture-idempotency",
                "target_id": target.target_id,
                "package_reference": {
                    "episode_version_id": package.episode_version_id,
                    "package_sha256": package_sha256_for(package, store),
                    "package_manifest_sha256": manifest_sha256_for(package),
                },
                "authorization": {
                    "actor_id": "operator-1",
                    "decision_id": "approval-1",
                    "reason": "approved for acceptance test",
                    "operation": "publish",
                },
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )


@given("a verified package and configured publication activity")
def configured_context(context: Any, tmp_path: Path) -> None:
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    package = _package(artifacts)
    target = _target()
    service = PublishingService(
        artifact_store=artifacts,
        adapters={"filesystem": FilesystemPublicationAdapter(tmp_path / "published")},
    )
    completion_calls: list[tuple[Any, Any]] = []

    def record_completion(request: Any, receipt: Any) -> None:
        completion_calls.append((request, receipt))

    context.values.update(
        artifacts=artifacts,
        package=package,
        target=target,
        service=service,
        completion_calls=completion_calls,
        activity=build_durable_publication_activity(
            service,
            request_resolver=build_publication_request_resolver(
                package_resolver=lambda episode_version_id: (
                    package
                    if episode_version_id == package.episode_version_id
                    else None
                ),
                target_resolver=lambda tenant_id, project_id, target_id: (
                    target
                    if (tenant_id, project_id, target_id)
                    == (TENANT, PROJECT, target.target_id)
                    else None
                ),
            ),
            publication_completion_recorder=record_completion,
        ),
    )


@when("the Temporal publish command runs")
def run_publish_workflow(context: Any) -> None:
    async def run() -> str:
        async with (
            retry_local_temporal_environment(
                WorkflowEnvironment.start_local
            ) as environment,
            Worker(
                environment.client,
                task_queue="poddown-publication-test",
                workflows=[EpisodeCommandWorkflow],
                activities=[context.values["activity"]],
            ),
        ):
            return await environment.client.execute_workflow(
                EpisodeCommandWorkflow.run,
                _command(
                    context.values["package"],
                    context.values["target"],
                    context.values["artifacts"],
                ),
                id="poddown-publication-test-workflow",
                task_queue="poddown-publication-test",
            )

    context.values["result"] = json.loads(asyncio.run(run()))


@then("the workflow returns one publication receipt")
def receipt_result(context: Any) -> None:
    receipt = context.values["result"]["receipt"]
    assert receipt["status"] == "published"
    assert receipt["target_id"] == "filesystem-target"
    assert len(context.values["completion_calls"]) == 1


@then("the published filesystem contains the exact episode bytes")
def published_bytes(context: Any) -> None:
    path = (
        context.values["target"]
        and context.values["service"].adapters["filesystem"].root
        / "tenants"
        / str(TENANT)
        / "projects"
        / str(PROJECT)
        / "filesystem-target"
        / str(EPISODE)
        / "episode.wav"
    )
    assert path.read_bytes() == b"exact episode bytes"
    assert sha256(path.read_bytes()).hexdigest() == next(
        reference.sha256
        for reference in context.values["package"].files
        if reference.name == "episode.wav"
    )
