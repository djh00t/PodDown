"""Unit tests for operational retention and recovery contracts."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

import poddown.operations as operations
from poddown.operations import (
    ArtifactScopeError,
    DeletionAuthorization,
    LifecycleArtifact,
    LifecycleManifest,
    ManifestIntegrityError,
    ManifestKind,
    RetentionAction,
    RetentionClass,
    RetentionEvaluator,
    RetentionPolicy,
)

TENANT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")
OTHER_PROJECT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b13")
CAPTURED = datetime(2026, 8, 1, tzinfo=UTC)
NOW = datetime(2026, 8, 14, tzinfo=UTC)


def policy() -> RetentionPolicy:
    return RetentionPolicy.from_days(
        {
            RetentionClass.SOURCE: 7,
            RetentionClass.AUDIO: 30,
            RetentionClass.TRANSCRIPT: 14,
            RetentionClass.PROVIDER_PAYLOAD: 1,
            RetentionClass.LOG: 90,
            RetentionClass.CONSENT_EVIDENCE: None,
            RetentionClass.PUBLICATION_EVIDENCE: 1,
        }
    )


def test_retention_is_independent_and_uses_expiry_boundary() -> None:
    evaluator = RetentionEvaluator(policy())
    source = evaluator.evaluate(
        data_class=RetentionClass.SOURCE,
        captured_at=CAPTURED,
        now=CAPTURED + timedelta(days=7),
    )
    audio = evaluator.evaluate(
        data_class=RetentionClass.AUDIO,
        captured_at=CAPTURED,
        now=NOW,
    )
    assert source.action is RetentionAction.DELETE_ELIGIBLE
    assert audio.action is RetentionAction.RETAIN


def test_immutable_evidence_requires_explicit_authorization_and_audit_reason() -> None:
    evaluator = RetentionEvaluator(policy())
    blocked = evaluator.evaluate(
        data_class=RetentionClass.PUBLICATION_EVIDENCE,
        captured_at=CAPTURED,
        now=NOW,
        immutable=True,
    )
    authorized = evaluator.evaluate(
        data_class=RetentionClass.PUBLICATION_EVIDENCE,
        captured_at=CAPTURED,
        now=NOW,
        immutable=True,
        authorization=DeletionAuthorization(
            authorization_id="approval-018f",
            approved_by="operator-1",
            reason="legal retention decision",
        ),
    )
    assert blocked.action is RetentionAction.BLOCKED
    assert authorized.action is RetentionAction.DELETE_ELIGIBLE
    assert "approval-018f" in authorized.reason


def test_legal_hold_blocks_even_with_immutable_deletion_authorization() -> None:
    decision = RetentionEvaluator(policy()).evaluate(
        data_class=RetentionClass.SOURCE,
        captured_at=CAPTURED,
        now=NOW,
        legal_hold=True,
        authorization=DeletionAuthorization(
            authorization_id="approval-018f",
            approved_by="operator-1",
            reason="approved",
        ),
    )
    assert decision.action is RetentionAction.BLOCKED
    assert "legal hold" in decision.reason


def test_policy_requires_every_retention_class_and_valid_days() -> None:
    with pytest.raises(ValueError, match="retention classes"):
        RetentionPolicy.from_days({RetentionClass.SOURCE: 1})
    values = {retention_class: 1 for retention_class in RetentionClass}
    values[RetentionClass.AUDIO] = -1
    with pytest.raises(ValueError, match="non-negative"):
        RetentionPolicy.from_days(values)


def _artifact(
    *,
    tenant_id: UUID = TENANT,
    project_id: UUID = PROJECT,
    name: str = "state/episodes.json",
    data: bytes = b"{}",
) -> LifecycleArtifact:
    return LifecycleArtifact(
        tenant_id=tenant_id,
        project_id=project_id,
        name=name,
        media_type="application/json",
        data=data,
    )


def test_manifest_is_deterministic_and_verifies_exact_bytes() -> None:
    created_at = datetime(2026, 8, 14, 1, 2, 3, tzinfo=UTC)
    artifacts = (_artifact(), _artifact(name="state/usage.json", data=b"usage"))
    first = LifecycleManifest.capture(
        kind=ManifestKind.EXPORT,
        tenant_id=TENANT,
        project_id=PROJECT,
        artifacts=artifacts,
        created_at=created_at,
    )
    second = LifecycleManifest.capture(
        kind=ManifestKind.EXPORT,
        tenant_id=TENANT,
        project_id=PROJECT,
        artifacts=tuple(reversed(artifacts)),
        created_at=created_at,
    )
    assert first == second
    first.verify(artifacts)
    assert first.to_dict()["manifest_sha256"] == first.manifest_sha256


def test_manifest_rejects_scope_path_and_tampering() -> None:
    with pytest.raises(ValueError, match="path"):
        _artifact(name="../outside.json")
    manifest = LifecycleManifest.capture(
        kind=ManifestKind.BACKUP,
        tenant_id=TENANT,
        project_id=PROJECT,
        artifacts=(_artifact(),),
        created_at=NOW,
    )
    with pytest.raises(ArtifactScopeError):
        manifest.verify((_artifact(project_id=OTHER_PROJECT),))
    with pytest.raises(ManifestIntegrityError):
        manifest.verify((_artifact(data=b"tampered"),))


def test_manifest_round_trip_rejects_digest_mutation() -> None:
    manifest = LifecycleManifest.capture(
        kind=ManifestKind.EXPORT,
        tenant_id=TENANT,
        project_id=PROJECT,
        artifacts=(_artifact(),),
        created_at=NOW,
    )
    payload = manifest.to_dict()
    payload["manifest_sha256"] = "0" * 64
    with pytest.raises(ManifestIntegrityError, match="manifest checksum"):
        LifecycleManifest.from_dict(payload)


def test_filesystem_archive_is_idempotent_and_restores_exact_bytes(tmp_path) -> None:
    artifact = _artifact(name="state/episodes.json", data=b"episodes")
    manifest = LifecycleManifest.capture(
        kind=ManifestKind.BACKUP,
        tenant_id=TENANT,
        project_id=PROJECT,
        artifacts=(artifact,),
        created_at=NOW,
    )
    archive = operations.FilesystemLifecycleArchive(tmp_path / "backup")  # type: ignore[attr-defined]

    archive.write(manifest, (artifact,))
    archive.write(manifest, (artifact,))

    restored_manifest, restored_artifacts = archive.restore()
    assert restored_manifest == manifest
    assert restored_artifacts == (artifact,)


@pytest.mark.parametrize(
    "relative_name",
    ["../outside", "nested/../escape"],
)
def test_filesystem_archive_rejects_unsafe_manifest_paths(tmp_path, relative_name):
    artifact = _artifact(name="safe.json")
    manifest = LifecycleManifest.capture(
        kind=ManifestKind.EXPORT,
        tenant_id=TENANT,
        project_id=PROJECT,
        artifacts=(artifact,),
        created_at=NOW,
    )
    root = tmp_path / "archive"
    archive = operations.FilesystemLifecycleArchive(root)  # type: ignore[attr-defined]
    archive.write(manifest, (artifact,))
    unsafe_path = root / "artifacts" / relative_name
    unsafe_path.parent.mkdir(parents=True, exist_ok=True)
    unsafe_path.write_bytes(b"escape")
    with pytest.raises(ManifestIntegrityError):
        archive.restore()


def test_filesystem_archive_rejects_unexpected_files(tmp_path) -> None:
    artifact = _artifact()
    manifest = LifecycleManifest.capture(
        kind=ManifestKind.EXPORT,
        tenant_id=TENANT,
        project_id=PROJECT,
        artifacts=(artifact,),
        created_at=NOW,
    )
    root = tmp_path / "archive"
    archive = operations.FilesystemLifecycleArchive(root)  # type: ignore[attr-defined]
    archive.write(manifest, (artifact,))
    (root / "unexpected.txt").write_bytes(b"unexpected")
    with pytest.raises(ManifestIntegrityError, match="unexpected"):
        archive.restore()
