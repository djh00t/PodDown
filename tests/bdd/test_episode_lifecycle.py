"""Executable acceptance tests for the offline episode lifecycle contract."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

import pytest
from pytest_bdd import given, scenarios, then, when

from poddown.episode_service import (
    EpisodeApplicationService,
    EpisodeCreateCommand,
    EpisodeNotFound,
    EpisodeServiceError,
    EpisodeState,
    EpisodeValidationError,
    IdempotencyConflict,
    InMemoryEpisodeRepository,
    InvalidEpisodeTransition,
    PublishAuthorizationError,
    VersionConflict,
)

scenarios("../features/episode_lifecycle.feature")

TENANT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
OTHER_TENANT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11")
PROJECT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")
IDEMPOTENCY_KEY = "episode-create-001"
EPISODE_ID = UUID("01986e76-4ec6-7a8f-8000-000000000001")
FIXED_CLOCK = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)
PROFILE = "technical-dialogue"
SOURCE_BYTES = (
    b"---\n"
    b"poddown:\n"
    b"  profile: technical-dialogue\n"
    b"---\n"
    b"# Deterministic lifecycle fixture\n"
)
CHANGED_SOURCE_BYTES = SOURCE_BYTES + b"\nChanged request content.\n"
PACKAGE_BYTES = b"verified immutable package bytes"
PACKAGE_SHA256 = sha256(PACKAGE_BYTES).hexdigest()
QA_EVIDENCE = {"gate": "final-master", "status": "pass"}


def _service() -> EpisodeApplicationService:
    """Build the deterministic, offline service boundary under test."""
    return EpisodeApplicationService(
        repository=InMemoryEpisodeRepository(),
        available_profiles={PROFILE},
        episode_id_factory=lambda: EPISODE_ID,
        clock=lambda: FIXED_CLOCK,
    )


def _command(
    *, source_bytes: bytes = SOURCE_BYTES, profile: str = PROFILE
) -> EpisodeCreateCommand:
    """Build a literal tenant-scoped creation command."""
    return EpisodeCreateCommand(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        idempotency_key=IDEMPOTENCY_KEY,
        source_bytes=source_bytes,
        profile_name=profile,
    )


def _create(context):
    service = context.values.setdefault("service", _service())
    episode = service.create_episode(_command())
    context.values["episode"] = episode
    return episode


def _capture(context, error_type, operation) -> None:
    """Store a typed structured lifecycle failure for BDD assertions."""
    with pytest.raises(error_type) as captured:
        operation()
    context.values["error"] = captured.value


def _assert_redacted_failure(context, *, status: int, code: str) -> None:
    failure = context.values["error"].failure
    assert failure.status == status
    assert failure.code == code
    serialized = failure.to_dict()
    assert SOURCE_BYTES.decode("utf-8") not in str(serialized)
    assert "Deterministic lifecycle fixture" not in str(serialized)


def _advance_to_rendered(context):
    service = context.values["service"]
    episode = context.values["episode"]
    scripted = service.transition(
        tenant_id=TENANT_ID,
        episode_id=episode.episode_id,
        target_state=EpisodeState.SCRIPTED,
        expected_version=episode.version,
    )
    rendered = service.transition(
        tenant_id=TENANT_ID,
        episode_id=episode.episode_id,
        target_state=EpisodeState.RENDERED,
        expected_version=scripted.version,
    )
    context.values["episode"] = rendered
    return rendered


def _advance_to_packaged(context):
    rendered = _advance_to_rendered(context)
    service = context.values["service"]
    qa_passed = service.transition(
        tenant_id=TENANT_ID,
        episode_id=rendered.episode_id,
        target_state=EpisodeState.QA_PASSED,
        expected_version=rendered.version,
        qa_evidence=QA_EVIDENCE,
    )
    packaged = service.transition(
        tenant_id=TENANT_ID,
        episode_id=rendered.episode_id,
        target_state=EpisodeState.PACKAGED,
        expected_version=qa_passed.version,
        package_sha256=PACKAGE_SHA256,
        package_bytes=PACKAGE_BYTES,
    )
    context.values["episode"] = packaged
    return packaged


@given("a tenant-scoped episode request with valid Markdown and profile")
def valid_tenant_request(context):
    context.values["service"] = _service()


@given("a tenant-scoped episode request with an unknown profile")
def unknown_profile_request(context):
    context.values["service"] = _service()
    context.values["command"] = _command(profile="unregistered-profile")


@given("a created tenant-scoped episode")
def created_tenant_episode(context):
    valid_tenant_request(context)
    _create(context)


@given("an episode advanced through rendering")
def rendered_episode(context):
    created_tenant_episode(context)
    _advance_to_rendered(context)


@given("a packaged tenant-scoped episode")
def packaged_episode(context):
    created_tenant_episode(context)
    _advance_to_packaged(context)


@when("the episode is created")
def create_episode(context):
    try:
        context.values["episode"] = context.values["service"].create_episode(
            context.values.get("command", _command())
        )
    except EpisodeServiceError as error:
        context.values["error"] = error


@when("the same episode request is created twice")
def replay_episode_create(context):
    first = context.values["service"].create_episode(_command())
    second = context.values["service"].create_episode(_command())
    context.values["first"] = first
    context.values["second"] = second


@when("the tenant reuses its idempotency key with different source bytes")
def conflicting_idempotency_key(context):
    _capture(
        context,
        IdempotencyConflict,
        lambda: context.values["service"].create_episode(
            _command(source_bytes=CHANGED_SOURCE_BYTES)
        ),
    )


@when("another tenant reads the episode")
def another_tenant_reads_episode(context):
    episode = context.values["episode"]
    _capture(
        context,
        EpisodeNotFound,
        lambda: context.values["service"].get_episode(
            tenant_id=OTHER_TENANT_ID,
            episode_id=episode.episode_id,
        ),
    )


@when("the episode is published from its initial state")
def publish_from_initial_state(context):
    episode = context.values["episode"]
    _capture(
        context,
        InvalidEpisodeTransition,
        lambda: context.values["service"].transition(
            tenant_id=TENANT_ID,
            episode_id=episode.episode_id,
            target_state=EpisodeState.PUBLISHED,
            expected_version=episode.version,
        ),
    )


@when("QA passes without evidence and packaging is attempted without a checksum")
def missing_qa_and_package_evidence(context):
    episode = context.values["episode"]
    service = context.values["service"]
    with pytest.raises(InvalidEpisodeTransition) as qa_error:
        service.transition(
            tenant_id=TENANT_ID,
            episode_id=episode.episode_id,
            target_state=EpisodeState.QA_PASSED,
            expected_version=episode.version,
        )
    with pytest.raises(InvalidEpisodeTransition) as package_error:
        service.transition(
            tenant_id=TENANT_ID,
            episode_id=episode.episode_id,
            target_state=EpisodeState.PACKAGED,
            expected_version=episode.version,
        )
    context.values["errors"] = (qa_error.value, package_error.value)


@when("publishing is requested without authorization and then with authorization")
def publish_with_and_without_authorization(context):
    episode = context.values["episode"]
    service = context.values["service"]
    with pytest.raises(PublishAuthorizationError) as captured:
        service.publish(
            tenant_id=TENANT_ID,
            episode_id=episode.episode_id,
            expected_version=episode.version,
            authorized=False,
        )
    context.values["error"] = captured.value
    context.values["published"] = service.publish(
        tenant_id=TENANT_ID,
        episode_id=episode.episode_id,
        expected_version=episode.version,
        authorized=True,
    )


@when("two transitions use the same expected version")
def stale_transition(context):
    episode = context.values["episode"]
    service = context.values["service"]
    context.values["updated"] = service.transition(
        tenant_id=TENANT_ID,
        episode_id=episode.episode_id,
        target_state=EpisodeState.SCRIPTED,
        expected_version=episode.version,
    )
    _capture(
        context,
        VersionConflict,
        lambda: service.transition(
            tenant_id=TENANT_ID,
            episode_id=episode.episode_id,
            target_state=EpisodeState.SCRIPTED,
            expected_version=episode.version,
        ),
    )


@then("the episode records the exact source SHA-256 and resolved profile")
def exact_source_snapshot(context):
    episode = context.values["episode"]
    assert episode.source_sha256 == sha256(SOURCE_BYTES).hexdigest()
    assert episode.profile_name == PROFILE
    assert episode.state is EpisodeState.VALIDATED
    assert episode.version == 1
    assert episode.created_at == FIXED_CLOCK


@then("creation returns a redacted validation failure with status 422")
def validation_failure(context):
    assert isinstance(context.values["error"], EpisodeValidationError)
    _assert_redacted_failure(context, status=422, code="episode_validation_failed")


@then("both creates return the same immutable episode at version 1")
def idempotent_replay(context):
    first = context.values["first"]
    second = context.values["second"]
    assert second == first
    assert second.episode_id == EPISODE_ID
    assert second.version == 1


@then("creation returns a redacted idempotency conflict with status 409")
def idempotency_conflict(context):
    _assert_redacted_failure(context, status=409, code="idempotency_conflict")


@then("the read returns a redacted not-found failure with status 404")
def tenant_not_found(context):
    _assert_redacted_failure(context, status=404, code="episode_not_found")


@then("transition returns a redacted invalid-transition failure with status 409")
def illegal_transition(context):
    _assert_redacted_failure(context, status=409, code="invalid_episode_transition")


@then(
    "both lifecycle gates return redacted invalid-transition failures with status 409"
)
def lifecycle_gates(context):
    for error in context.values["errors"]:
        context.values["error"] = error
        _assert_redacted_failure(
            context,
            status=409,
            code="invalid_episode_transition",
        )


@then("unauthorized publish returns a redacted authorization failure with status 403")
def unauthorized_publish(context):
    _assert_redacted_failure(context, status=403, code="publish_not_authorized")


@then("authorized publish preserves the immutable package checksum")
def authorized_publish(context):
    published = context.values["published"]
    assert published.state is EpisodeState.PUBLISHED
    assert published.package_sha256 == PACKAGE_SHA256
    assert published.version == 6


@then(
    "the stale transition returns a redacted version-conflict failure with status 409"
)
def version_conflict(context):
    _assert_redacted_failure(context, status=409, code="episode_version_conflict")
    assert context.values["updated"].state is EpisodeState.SCRIPTED
    assert context.values["updated"].version == 2
