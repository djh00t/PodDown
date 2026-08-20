"""BDD bindings for tenant-safe retention, export, and restore contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pytest_bdd import given, scenarios, then, when

import poddown.operations as operations
from poddown.operations import (
    ArtifactScopeError,
    LifecycleArtifact,
    LifecycleManifest,
    ManifestIntegrityError,
    ManifestKind,
    RetentionAction,
    RetentionClass,
    RetentionEvaluator,
    RetentionPolicy,
)

scenarios("../features/operational_lifecycle.feature")

TENANT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")
OTHER_TENANT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b13")
CAPTURED = datetime(2026, 8, 1, tzinfo=UTC)
NOW = datetime(2026, 8, 14, tzinfo=UTC)


@given("a retention policy with independent source and audio windows")
def retention_policy(context: dict[str, object]) -> None:
    context.values["policy"] = RetentionPolicy.from_days(
        {
            RetentionClass.SOURCE: 7,
            RetentionClass.AUDIO: 30,
            RetentionClass.TRANSCRIPT: 14,
            RetentionClass.PROVIDER_PAYLOAD: 1,
            RetentionClass.LOG: 90,
            RetentionClass.CONSENT_EVIDENCE: None,
            RetentionClass.PUBLICATION_EVIDENCE: None,
        }
    )


@when("source data is evaluated after its retention window")
def evaluate_source(context: dict[str, object]) -> None:
    context.values["decision"] = RetentionEvaluator(
        context.values["policy"]  # type: ignore[arg-type]
    ).evaluate(
        data_class=RetentionClass.SOURCE,
        captured_at=CAPTURED,
        now=NOW,
    )


@then("the source deletion decision is eligible")
def source_is_eligible(context: dict[str, object]) -> None:
    assert context.values["decision"].action is RetentionAction.DELETE_ELIGIBLE  # type: ignore[union-attr]


@given("expired publication evidence without a deletion authorization")
def immutable_publication(context: dict[str, object]) -> None:
    policy = RetentionPolicy.from_days(
        {retention_class: 1 for retention_class in RetentionClass}
    )
    context.values["decision"] = RetentionEvaluator(policy).evaluate(
        data_class=RetentionClass.PUBLICATION_EVIDENCE,
        captured_at=CAPTURED,
        now=NOW,
        immutable=True,
    )


@when("the publication retention decision is evaluated")
def publication_evaluated(context: dict[str, object]) -> None:
    return


@then("deletion is blocked with an audit reason")
def publication_is_blocked(context: dict[str, object]) -> None:
    decision = context.values["decision"]
    assert decision.action is RetentionAction.BLOCKED  # type: ignore[union-attr]
    assert "authorization" in decision.reason  # type: ignore[union-attr]


@given("two scoped export artifacts for one tenant and project")
def export_artifacts(context: dict[str, object]) -> None:
    context.values["artifacts"] = (
        LifecycleArtifact(
            tenant_id=TENANT,
            project_id=PROJECT,
            name="database/episodes.json",
            media_type="application/json",
            data=b'{"episode":"one"}',
        ),
        LifecycleArtifact(
            tenant_id=TENANT,
            project_id=PROJECT,
            name="objects/episode.mp3",
            media_type="audio/mpeg",
            data=b"audio-bytes",
        ),
    )


@when("an export manifest is captured")
def capture_export(context: dict[str, object]) -> None:
    context.values["manifest"] = LifecycleManifest.capture(
        kind=ManifestKind.EXPORT,
        tenant_id=TENANT,
        project_id=PROJECT,
        artifacts=context.values["artifacts"],  # type: ignore[arg-type]
        created_at=NOW,
    )


@then("the manifest verifies the original artifacts")
def verify_original_export(context: dict[str, object]) -> None:
    manifest = context.values["manifest"]
    manifest.verify(context.values["artifacts"])  # type: ignore[union-attr]


@then("a changed artifact fails manifest verification")
def changed_export_fails(context: dict[str, object]) -> None:
    artifacts = list(context.values["artifacts"])  # type: ignore[arg-type]
    artifacts[1] = LifecycleArtifact(
        tenant_id=TENANT,
        project_id=PROJECT,
        name="objects/episode.mp3",
        media_type="audio/mpeg",
        data=b"changed",
    )
    with pytest.raises(ManifestIntegrityError):
        context.values["manifest"].verify(artifacts)  # type: ignore[union-attr]


@given("a backup manifest for one tenant and project")
def backup_manifest(context: dict[str, object]) -> None:
    artifact = LifecycleArtifact(
        tenant_id=TENANT,
        project_id=PROJECT,
        name="database/backup.dump",
        media_type="application/octet-stream",
        data=b"backup",
    )
    context.values["manifest"] = LifecycleManifest.capture(
        kind=ManifestKind.BACKUP,
        tenant_id=TENANT,
        project_id=PROJECT,
        artifacts=(artifact,),
        created_at=NOW,
    )
    context.values["artifact"] = artifact


@when("a restore is attempted with an artifact from another tenant")
def cross_tenant_restore(context: dict[str, object]) -> None:
    artifact = context.values["artifact"]
    context.values["restore_error"] = None
    try:
        context.values["manifest"].verify(  # type: ignore[union-attr]
            (
                LifecycleArtifact(
                    tenant_id=OTHER_TENANT,
                    project_id=PROJECT,
                    name=artifact.name,  # type: ignore[union-attr]
                    media_type=artifact.media_type,  # type: ignore[union-attr]
                    data=artifact.data,  # type: ignore[union-attr]
                ),
            )
        )
    except (ArtifactScopeError, ManifestIntegrityError) as error:
        context.values["restore_error"] = error


@then("restore verification fails closed")
def restore_fails(context: dict[str, object]) -> None:
    assert isinstance(context.values["restore_error"], ArtifactScopeError)


@when("a filesystem export archive is written and restored")
def filesystem_archive_round_trip(context: dict[str, object], tmp_path) -> None:
    artifacts = context.values["artifacts"]
    manifest = LifecycleManifest.capture(
        kind=ManifestKind.EXPORT,
        tenant_id=TENANT,
        project_id=PROJECT,
        artifacts=artifacts,  # type: ignore[arg-type]
        created_at=NOW,
    )
    root = tmp_path / "export"
    operations.FilesystemLifecycleArchive(root).write(  # type: ignore[attr-defined]
        manifest, artifacts
    )  # type: ignore[arg-type]
    restored_manifest, restored_artifacts = operations.FilesystemLifecycleArchive(  # type: ignore[attr-defined]
        root
    ).restore()
    context.values["archive_root"] = root
    context.values["manifest"] = manifest
    context.values["restored_manifest"] = restored_manifest
    context.values["restored_artifacts"] = restored_artifacts


@then("the restored archive matches the exact manifest and artifacts")
def filesystem_archive_matches(context: dict[str, object]) -> None:
    assert context.values["restored_manifest"] == context.values["manifest"]
    assert context.values["restored_artifacts"] == context.values["artifacts"]


@then("a tampered filesystem archive fails closed")
def tampered_filesystem_archive_fails(context: dict[str, object]) -> None:
    archive_root = context.values["archive_root"]
    (archive_root / "artifacts" / "objects" / "episode.mp3").write_bytes(b"tampered")  # type: ignore[union-attr]
    with pytest.raises(ManifestIntegrityError):
        operations.FilesystemLifecycleArchive(archive_root).restore()  # type: ignore[attr-defined,arg-type]
