"""Unit contracts for the offline tenant-scoped episode lifecycle."""

from __future__ import annotations

import json
import math
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

import pytest

from poddown.episode_service import (
    EpisodeApplicationService,
    EpisodeCreateCommand,
    EpisodeNotFound,
    EpisodeState,
    EpisodeValidationError,
    IdempotencyConflict,
    InMemoryEpisodeRepository,
    InvalidEpisodeTransition,
    PublishAuthorizationError,
    StructuredFailure,
    VersionConflict,
)

TENANT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
OTHER_TENANT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11")
PROJECT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")
EPISODE_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b13")
CREATED_AT = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
SOURCE = b"---\npoddown:\n  profile: spoken-word\n---\n# Tenant-scoped episode\n"
PACKAGE_CHECKSUM = sha256(b"verified immutable package bytes").hexdigest()
PACKAGE_BYTES = b"verified immutable package bytes"
PACKAGE_MANIFEST_SHA256 = "b" * 64


def _service() -> EpisodeApplicationService:
    """Build the deterministic, offline lifecycle boundary."""
    return EpisodeApplicationService(
        repository=InMemoryEpisodeRepository(),
        episode_id_factory=lambda: EPISODE_ID,
        clock=lambda: CREATED_AT,
        available_profiles=frozenset({"spoken-word"}),
    )


def _command(**overrides: object) -> EpisodeCreateCommand:
    """Build one valid creation command with literal input evidence."""
    values: dict[str, object] = {
        "tenant_id": TENANT_ID,
        "project_id": PROJECT_ID,
        "idempotency_key": "episode-create-001",
        "source_bytes": SOURCE,
        "profile_name": "spoken-word",
    }
    values.update(overrides)
    return EpisodeCreateCommand(**values)  # type: ignore[arg-type]


def _created(service: EpisodeApplicationService | None = None):
    """Create one valid episode in the supplied deterministic service."""
    return (service or _service()).create_episode(_command())


@pytest.mark.parametrize(
    "overrides",
    (
        {"tenant_id": UUID("123e4567-e89b-12d3-a456-426614174000")},
        {"project_id": UUID("123e4567-e89b-12d3-a456-426614174000")},
        {"idempotency_key": ""},
        {"source_bytes": "not bytes"},
        {"profile_name": "missing-profile"},
    ),
    ids=("tenant-not-v7", "project-not-v7", "empty-key", "non-bytes", "profile"),
)
def test_create_rejects_noncanonical_or_invalid_commands(overrides):
    """Invalid commands must stop before an episode can be persisted."""
    with pytest.raises(EpisodeValidationError):
        _service().create_episode(_command(**overrides))


def test_validation_failure_redacts_invalid_frontmatter_values():
    """Parser detail must not expose arbitrary frontmatter values in API errors."""
    source = (
        b"---\n"
        b"poddown:\n"
        b"  profile: spoken-word\n"
        b"  target_minutes: frontmatter-secret\n"
        b"---\n"
        b"# Invalid metadata\n"
    )

    with pytest.raises(EpisodeValidationError) as raised:
        _service().create_episode(_command(source_bytes=source))

    assert "frontmatter-secret" not in str(raised.value)
    assert "frontmatter-secret" not in str(raised.value.failure.to_dict())


def test_structured_failure_rejects_nonfinite_json_numbers():
    """Failure evidence must remain strict JSON rather than emitting NaN/Infinity."""
    with pytest.raises(ValueError, match="finite"):
        StructuredFailure(
            code="invalid_evidence",
            stage="validation",
            message="evidence is invalid",
            retriable=False,
            details={"score": math.nan},
        )

    failure = StructuredFailure(
        code="valid_evidence",
        stage="validation",
        message="evidence is valid",
        retriable=False,
        details={"score": 1.0},
    )
    json.dumps(failure.to_dict(), allow_nan=False)


def test_create_uses_the_injected_uuidv7_factory_and_exact_source_fingerprint():
    """Changing generated identity or original source bytes must break provenance."""
    record = _created()

    assert record.episode_id == EPISODE_ID
    assert record.source_sha256 == sha256(SOURCE).hexdigest()
    assert (
        record.request_fingerprint
        == sha256(
            b"018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10\x00"
            b"018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12\x00"
            b"spoken-word\x00" + SOURCE
        ).hexdigest()
    )


def test_episode_records_are_immutable_snapshots():
    """A persisted lifecycle record cannot be modified by a caller."""
    record = _created()

    with pytest.raises(FrozenInstanceError):
        record.state = EpisodeState.PUBLISHED


def test_get_hides_another_tenants_episode_as_not_found():
    """Changing tenant scope must not disclose whether an episode exists."""
    service = _service()
    record = _created(service)

    with pytest.raises(EpisodeNotFound):
        service.get_episode(OTHER_TENANT_ID, record.episode_id)


def test_identical_idempotency_replay_returns_the_original_immutable_record():
    """A retry must neither create nor mutate a second episode."""
    service = _service()
    first = _created(service)
    replay = service.create_episode(_command())

    assert replay is first
    assert replay.version == 1


def test_idempotency_replay_preserves_the_original_create_snapshot_after_transition():
    """A create retry must return validation state, not the current lifecycle state."""
    service = _service()
    created = _created(service)
    service.transition(
        TENANT_ID,
        created.episode_id,
        EpisodeState.SCRIPTED,
        expected_version=created.version,
    )

    replay = service.create_episode(_command())

    assert replay == created
    assert replay.state is EpisodeState.VALIDATED
    assert replay.version == 1


def test_create_records_the_profile_resolved_from_frontmatter():
    """A document profile must take precedence over the command default profile."""
    service = EpisodeApplicationService(
        repository=InMemoryEpisodeRepository(),
        episode_id_factory=lambda: EPISODE_ID,
        clock=lambda: CREATED_AT,
        available_profiles=frozenset({"spoken-word", "technical-dialogue"}),
    )
    source = SOURCE.replace(b"spoken-word", b"technical-dialogue")

    record = service.create_episode(_command(source_bytes=source))

    assert record.profile_name == "technical-dialogue"


def test_create_rejects_invalid_crlf_frontmatter():
    """CRLF frontmatter must receive the same strict validation as LF frontmatter."""
    source = (
        b"---\r\n"
        b"poddown:\r\n"
        b"  profile: spoken-word\r\n"
        b"  unexpected: forbidden\r\n"
        b"---\r\n"
        b"# Invalid CRLF metadata\r\n"
    )

    with pytest.raises(EpisodeValidationError, match="Unknown PodDown key"):
        _service().create_episode(_command(source_bytes=source))


def test_idempotency_key_reuse_with_a_different_fingerprint_fails_closed():
    """Changing source evidence behind a key must not replace the first request."""
    service = _service()
    original = _created(service)

    with pytest.raises(IdempotencyConflict):
        service.create_episode(_command(source_bytes=SOURCE + b"\n"))

    assert service.get_episode(TENANT_ID, original.episode_id) == original


def test_repository_rejects_mutation_of_immutable_identity_fields():
    """Repository indexes must reject tampered immutable identity fields."""
    repository = InMemoryEpisodeRepository()
    service = EpisodeApplicationService(
        repository=repository,
        episode_id_factory=lambda: EPISODE_ID,
        clock=lambda: CREATED_AT,
        available_profiles=frozenset({"spoken-word"}),
    )
    record = _created(service)
    tampered = replace(record, project_id=OTHER_TENANT_ID)

    with pytest.raises(ValueError, match="identity fields are immutable"):
        repository.replace(
            TENANT_ID,
            tampered,
            expected_version=record.version,
        )

    assert repository.get(TENANT_ID, record.episode_id) == record


def test_transition_matrix_rejects_skips_regressions_and_publish_before_qa():
    """Illegal lifecycle edges must not advance a record."""
    service = _service()
    record = _created(service)

    with pytest.raises(InvalidEpisodeTransition):
        service.transition(
            TENANT_ID,
            record.episode_id,
            EpisodeState.PUBLISHED,
            expected_version=record.version,
        )

    staged = service.transition(
        TENANT_ID,
        record.episode_id,
        EpisodeState.SCRIPTED,
        expected_version=record.version,
    )
    with pytest.raises(InvalidEpisodeTransition):
        service.transition(
            TENANT_ID,
            staged.episode_id,
            EpisodeState.VALIDATED,
            expected_version=staged.version,
        )


@pytest.mark.parametrize(
    "qa_evidence",
    (None, {"status": "fail"}),
    ids=("missing-qa", "failed-qa"),
)
def test_qa_requires_passing_evidence(qa_evidence):
    """Missing evidence or a failed QA gate must prevent lifecycle progress."""
    service = _service()
    record = _created(service)
    scripted = service.transition(
        TENANT_ID,
        record.episode_id,
        EpisodeState.SCRIPTED,
        expected_version=record.version,
    )

    rendered = service.transition(
        TENANT_ID,
        scripted.episode_id,
        EpisodeState.RENDERED,
        expected_version=scripted.version,
    )
    with pytest.raises(InvalidEpisodeTransition):
        service.transition(
            TENANT_ID,
            rendered.episode_id,
            EpisodeState.QA_PASSED,
            expected_version=rendered.version,
            qa_evidence=qa_evidence,
        )


def test_packaging_requires_a_checksum_after_passing_qa():
    """A passing QA record cannot package without immutable artifact evidence."""
    service = _service()
    record = _created(service)
    scripted = service.transition(
        TENANT_ID,
        record.episode_id,
        EpisodeState.SCRIPTED,
        expected_version=record.version,
    )
    rendered = service.transition(
        TENANT_ID,
        record.episode_id,
        EpisodeState.RENDERED,
        expected_version=scripted.version,
    )
    qa_passed = service.transition(
        TENANT_ID,
        record.episode_id,
        EpisodeState.QA_PASSED,
        expected_version=rendered.version,
        qa_evidence={"status": "pass"},
    )

    with pytest.raises(InvalidEpisodeTransition):
        service.transition(
            TENANT_ID,
            qa_passed.episode_id,
            EpisodeState.PACKAGED,
            expected_version=qa_passed.version,
        )


def test_packaging_rejects_bytes_that_do_not_match_the_declared_checksum():
    """Package identity must bind to the exact bytes supplied at the boundary."""
    service = _service()
    record = _created(service)
    scripted = service.transition(
        TENANT_ID,
        record.episode_id,
        EpisodeState.SCRIPTED,
        expected_version=record.version,
    )
    rendered = service.transition(
        TENANT_ID,
        record.episode_id,
        EpisodeState.RENDERED,
        expected_version=scripted.version,
    )
    qa_passed = service.transition(
        TENANT_ID,
        record.episode_id,
        EpisodeState.QA_PASSED,
        expected_version=rendered.version,
        qa_evidence={"status": "pass"},
    )

    with pytest.raises(InvalidEpisodeTransition, match="do not match"):
        service.transition(
            TENANT_ID,
            qa_passed.episode_id,
            EpisodeState.PACKAGED,
            expected_version=qa_passed.version,
            package_sha256=PACKAGE_CHECKSUM,
            package_bytes=b"tampered package bytes",
        )


def test_record_package_completion_projects_and_replays_verified_worker_package():
    """A worker completion must persist package identity through the lifecycle."""
    service = _service()
    record = _created(service)

    packaged = service.record_package_completion(
        TENANT_ID,
        record.episode_id,
        qa_evidence={"status": "pass", "critical_token_accuracy": 1.0},
        package_sha256=PACKAGE_CHECKSUM,
        package_manifest_sha256=PACKAGE_MANIFEST_SHA256,
    )
    replayed = service.record_package_completion(
        TENANT_ID,
        record.episode_id,
        qa_evidence={"status": "pass", "critical_token_accuracy": 1.0},
        package_sha256=PACKAGE_CHECKSUM,
        package_manifest_sha256=PACKAGE_MANIFEST_SHA256,
    )

    assert packaged.state is EpisodeState.PACKAGED
    assert packaged.version == 5
    assert packaged.package_sha256 == PACKAGE_CHECKSUM
    assert packaged.package_manifest_sha256 == PACKAGE_MANIFEST_SHA256
    assert replayed == packaged


def test_record_package_completion_rejects_a_conflicting_replay():
    """A package retry cannot silently replace durable immutable identity."""
    service = _service()
    record = _created(service)
    service.record_package_completion(
        TENANT_ID,
        record.episode_id,
        qa_evidence={"status": "pass"},
        package_sha256=PACKAGE_CHECKSUM,
        package_manifest_sha256=PACKAGE_MANIFEST_SHA256,
    )

    with pytest.raises(InvalidEpisodeTransition, match="conflicts"):
        service.record_package_completion(
            TENANT_ID,
            record.episode_id,
            qa_evidence={"status": "pass"},
            package_sha256=sha256(b"a different package").hexdigest(),
            package_manifest_sha256="c" * 64,
        )


def test_failed_transition_preserves_structured_redacted_failure_details():
    """Failure status must be actionable without retaining source text or secrets."""
    service = _service()
    record = _created(service)
    failure = StructuredFailure(
        code="package_checksum_mismatch",
        stage="packaging",
        message="package integrity verification failed",
        retriable=False,
        details={"expected_checksum": "a" * 64},
    )

    failed = service.transition(
        TENANT_ID,
        record.episode_id,
        EpisodeState.FAILED,
        expected_version=record.version,
        failure=failure,
    )

    assert failed.failure == failure
    assert SOURCE.decode("utf-8") not in repr(failed.failure)
    assert "api_key" not in repr(failed.failure).casefold()


def test_publish_requires_explicit_authorization_and_preserves_package_identity():
    """Unauthorized publication must not alter a QA-approved package."""
    service = _service()
    record = _created(service)
    scripted = service.transition(
        TENANT_ID,
        record.episode_id,
        EpisodeState.SCRIPTED,
        expected_version=record.version,
    )
    rendered = service.transition(
        TENANT_ID,
        record.episode_id,
        EpisodeState.RENDERED,
        expected_version=scripted.version,
    )
    qa_passed = service.transition(
        TENANT_ID,
        record.episode_id,
        EpisodeState.QA_PASSED,
        expected_version=rendered.version,
        qa_evidence={"status": "pass"},
    )
    packaged = service.transition(
        TENANT_ID,
        qa_passed.episode_id,
        EpisodeState.PACKAGED,
        expected_version=qa_passed.version,
        package_sha256=PACKAGE_CHECKSUM,
        package_bytes=PACKAGE_BYTES,
        package_manifest_sha256=PACKAGE_MANIFEST_SHA256,
    )

    assert packaged.package_sha256 == PACKAGE_CHECKSUM
    assert packaged.package_manifest_sha256 == PACKAGE_MANIFEST_SHA256

    with pytest.raises(PublishAuthorizationError):
        service.publish(
            TENANT_ID,
            packaged.episode_id,
            expected_version=packaged.version,
            authorized=False,
        )

    published = service.publish(
        TENANT_ID,
        packaged.episode_id,
        expected_version=packaged.version,
        authorized=True,
    )

    assert published.state is EpisodeState.PUBLISHED
    assert published.package_sha256 == PACKAGE_CHECKSUM
    assert published.package_manifest_sha256 == PACKAGE_MANIFEST_SHA256


def test_transition_rejects_a_stale_optimistic_version_without_mutating_record():
    """A concurrent writer must not overwrite a newer immutable snapshot."""
    service = _service()
    record = _created(service)
    advanced = service.transition(
        TENANT_ID,
        record.episode_id,
        EpisodeState.SCRIPTED,
        expected_version=record.version,
    )

    with pytest.raises(VersionConflict):
        service.transition(
            TENANT_ID,
            record.episode_id,
            EpisodeState.FAILED,
            expected_version=record.version,
            failure=StructuredFailure(
                code="stale_writer",
                stage="packaging",
                message="should not be stored",
                retriable=True,
                details={},
            ),
        )

    assert service.get_episode(TENANT_ID, record.episode_id) == advanced


def test_default_id_factory_generates_uuidv7_episode_identity():
    """The production default must satisfy the UUIDv7 identifier contract."""
    service = EpisodeApplicationService(
        repository=InMemoryEpisodeRepository(),
        clock=lambda: CREATED_AT,
        available_profiles=frozenset({"spoken-word"}),
    )

    record = service.create_episode(_command())

    assert record.episode_id.version == 7


def test_service_rejects_invalid_configured_profile_names():
    """Profile registries must fail closed before accepting a request."""
    with pytest.raises(ValueError, match="available_profiles"):
        EpisodeApplicationService(
            repository=InMemoryEpisodeRepository(),
            clock=lambda: CREATED_AT,
            available_profiles={"Not a profile"},
        )


def test_status_dict_is_json_safe_and_contains_no_source_bytes():
    """Status serialization must expose evidence, never canonical source text."""
    record = _created()

    status = record.status_dict()
    json.dumps(status, allow_nan=False)

    assert SOURCE.decode("utf-8") not in str(status)
    assert status["source_sha256"] == sha256(SOURCE).hexdigest()


def test_repository_rejects_episode_id_collision_for_a_new_idempotency_key():
    """A reused episode identity cannot create a second idempotency index entry."""
    repository = InMemoryEpisodeRepository()
    service = EpisodeApplicationService(
        repository=repository,
        episode_id_factory=lambda: EPISODE_ID,
        clock=lambda: CREATED_AT,
        available_profiles=frozenset({"spoken-word"}),
    )
    record = _created(service)
    collision = replace(record, idempotency_key="another-key")

    with pytest.raises(IdempotencyConflict):
        repository.create(collision)


def test_transition_rejects_malformed_evidence_arguments():
    """Evidence belongs only to its corresponding lifecycle gate."""
    service = _service()
    record = _created(service)

    with pytest.raises(InvalidEpisodeTransition):
        service.transition(
            TENANT_ID,
            record.episode_id,
            EpisodeState.SCRIPTED,
            expected_version=record.version,
            qa_evidence={"status": "pass"},
        )
    with pytest.raises(InvalidEpisodeTransition):
        service.transition(
            TENANT_ID,
            record.episode_id,
            EpisodeState.FAILED,
            expected_version=record.version,
        )
    with pytest.raises(VersionConflict):
        service.transition(
            TENANT_ID,
            record.episode_id,
            EpisodeState.SCRIPTED,
            expected_version=0,
        )


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        (
            b"---\npoddown:\n  profile: spoken-word\n  unexpected: secret\n---\n# x\n",
            "Unknown PodDown key: unexpected",
        ),
        (b"---\npoddown: [\n---\n# x\n", "Invalid YAML frontmatter"),
        (b"---\npoddown:\n  profile: spoken-word\n", "Invalid Markdown frontmatter"),
    ),
    ids=("unknown-key", "yaml", "unterminated"),
)
def test_validation_error_messages_use_stable_redacted_categories(source, expected):
    """Parser-specific detail must not become an API response contract."""
    with pytest.raises(EpisodeValidationError) as raised:
        _service().create_episode(_command(source_bytes=source))

    assert str(raised.value) == expected
