"""BDD bindings for the planned offline episode HTTP API contract."""

from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient
from pytest_bdd import given, scenarios, then, when

from poddown.api import create_app
from poddown.episode_service import (
    EpisodeApplicationService,
    EpisodeCreateCommand,
    EpisodeState,
    InMemoryEpisodeRepository,
    StructuredFailure,
)
from tests.integration.test_episode_api import (
    OTHER_TENANT_ID,
    PROFILE,
    PROJECT_ID,
    SOURCE,
    TENANT_ID,
    UUIDV7,
    _assert_problem,
    _headers,
)

scenarios("../features/episode_api.feature")


def _client(context) -> TestClient:
    return context.values.setdefault("client", TestClient(create_app()))


def _create(context) -> dict:
    response = _client(context).post(
        "/v1/episodes", headers=_headers(), json={"source": SOURCE, "profile": PROFILE}
    )
    assert response.status_code == 202
    return response.json()


@given("an offline episode API client with valid tenant headers")
def offline_client(context) -> None:
    """The app factory must select offline in-memory dependencies for these tests."""
    _client(context)


@given("an offline episode API client with a created episode")
def created_episode_client(context) -> None:
    offline_client(context)
    context.values["created"] = _create(context)


@given("an offline episode API client with a failed episode")
def failed_episode_client(context) -> None:
    repository = InMemoryEpisodeRepository()
    service = EpisodeApplicationService(
        repository=repository,
        available_profiles={PROFILE},
    )
    record = service.create_episode(
        EpisodeCreateCommand(
            tenant_id=UUID(TENANT_ID),
            project_id=UUID(PROJECT_ID),
            idempotency_key="failed-bdd-001",
            source_bytes=SOURCE.encode("utf-8"),
            profile_name=PROFILE,
        )
    )
    service.transition(
        UUID(TENANT_ID),
        record.episode_id,
        EpisodeState.FAILED,
        expected_version=record.version,
        failure=StructuredFailure(
            code="provider_failure",
            stage="render",
            message="provider failed for SECRET_SOURCE",
            retriable=False,
            details={"raw_source": "SECRET_SOURCE", "credential": "TOKEN"},
            status=502,
        ),
    )
    context.values["client"] = TestClient(create_app(service))
    context.values["failed_episode_id"] = str(record.episode_id)


@when("I submit valid Markdown for the registered profile")
def submit_valid_markdown(context) -> None:
    context.values["response"] = _client(context).post(
        "/v1/episodes", headers=_headers(), json={"source": SOURCE, "profile": PROFILE}
    )


@then("the create response is accepted with a UUIDv7 summary and command receipt")
def accepted_create(context) -> None:
    response = context.values["response"]
    assert response.status_code == 202
    body = response.json()
    assert UUIDV7.fullmatch(body["episode"]["id"])
    assert body["receipt"]["episode_id"] == body["episode"]["id"]
    assert body["receipt"]["idempotency_key"] == "episode-create-001"


@when("I submit the same create request twice")
def replay_create(context) -> None:
    context.values["first"] = _create(context)
    context.values["second"] = _create(context)


@then("both create responses describe the same episode and receipt")
def same_create(context) -> None:
    assert context.values["second"] == context.values["first"]


@when("I reuse the create idempotency key with different Markdown")
def conflict_create(context) -> None:
    _create(context)
    context.values["response"] = _client(context).post(
        "/v1/episodes",
        headers=_headers(),
        json={"source": SOURCE + "Changed", "profile": PROFILE},
    )


@then("the response is a stable redacted conflict problem")
def idempotency_problem(context) -> None:
    _assert_problem(context.values["response"], status=409, code="idempotency_conflict")


@when("I omit or invalidate tenant project idempotency profile or source encoding")
def invalid_context(context) -> None:
    client = _client(context)
    requests = [
        client.post(
            "/v1/episodes",
            headers={"X-Project-ID": "bad", "Idempotency-Key": "key"},
            json={"source": SOURCE, "profile": PROFILE},
        ),
        client.post(
            "/v1/episodes",
            headers={"X-Tenant-ID": TENANT_ID, "X-Project-ID": PROJECT_ID},
            json={"source": SOURCE, "profile": PROFILE},
        ),
        client.post(
            "/v1/episodes",
            headers=_headers(),
            json={"source": SOURCE, "profile": "unknown"},
        ),
        client.post(
            "/v1/episodes",
            headers=_headers(key="missing-profile"),
            json={"source": SOURCE},
        ),
        client.post(
            "/v1/episodes",
            headers=_headers(),
            content=b'{"source":"\xff","profile":"technical-dialogue"}',
        ),
    ]
    context.values["responses"] = requests


@then("every response is a stable redacted client problem")
def invalid_context_problem(context) -> None:
    for response in context.values["responses"]:
        assert response.status_code in {400, 422}
        _assert_problem(response, status=response.status_code)


@when("another tenant gets the episode status render and publish routes")
def cross_tenant_routes(context) -> None:
    episode_id = context.values["created"]["episode"]["id"]
    client = _client(context)
    headers = _headers(tenant_id=OTHER_TENANT_ID, key="other-tenant")
    context.values["responses"] = [
        client.get(f"/v1/episodes/{episode_id}", headers=headers),
        client.get(f"/v1/episodes/{episode_id}/status", headers=headers),
        client.post(f"/v1/episodes/{episode_id}/render", headers=headers),
        client.post(f"/v1/episodes/{episode_id}/publish", headers=headers),
    ]


@then("every cross-tenant response is a redacted not-found problem")
def cross_tenant_problem(context) -> None:
    for response in context.values["responses"]:
        _assert_problem(response, status=404, code="episode_not_found")


@when("I request the episode status")
def request_status(context) -> None:
    episode_id = context.values["created"]["episode"]["id"]
    context.values["response"] = _client(context).get(
        f"/v1/episodes/{episode_id}/status", headers=_headers(key="status-001")
    )


@then("the status response contains no source or credential material")
def status_redaction(context) -> None:
    response = context.values["response"]
    assert response.status_code == 200
    assert SOURCE not in str(response.json())
    assert "authorization" not in str(response.json()).lower()


@then("the status response contains the current episode version")
def status_version(context) -> None:
    response = context.values["response"]
    assert response.status_code == 200
    assert response.json()["version"] == 1


@when("I request the failed episode status")
def request_failed_status(context) -> None:
    context.values["response"] = _client(context).get(
        f"/v1/episodes/{context.values['failed_episode_id']}/status",
        headers=_headers(key="failed-bdd-read-001"),
    )


@then("the failure response contains only allowlisted fields")
def failure_redaction(context) -> None:
    response = context.values["response"]
    assert response.status_code == 200
    assert response.json()["failure"] == {
        "code": "provider_failure",
        "stage": "render",
        "status": 502,
        "retriable": False,
    }
    assert "SECRET_SOURCE" not in str(response.json())
    assert "TOKEN" not in str(response.json())


@when("I submit the same render request twice")
def replay_render(context) -> None:
    episode_id = context.values["created"]["episode"]["id"]
    headers = _headers(key="render-001")
    context.values["first"] = _client(context).post(
        f"/v1/episodes/{episode_id}/render", headers=headers
    )
    context.values["second"] = _client(context).post(
        f"/v1/episodes/{episode_id}/render", headers=headers
    )


@then("both render responses return the same queued command receipt")
def queued_render(context) -> None:
    first = context.values["first"]
    second = context.values["second"]
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert first.json()["state"] == "queued"


@when("I publish without explicit authorization and then with authorization")
def publish_authorization(context) -> None:
    episode_id = context.values["created"]["episode"]["id"]
    headers = _headers(key="publish-001")
    context.values["unauthorized"] = _client(context).post(
        f"/v1/episodes/{episode_id}/publish", headers=headers
    )
    context.values["authorized"] = _client(context).post(
        f"/v1/episodes/{episode_id}/publish",
        headers={**headers, "X-Publish-Authorization": "true"},
    )


@then("publish is forbidden without authorization and remains gated before packaging")
def publish_authorization_result(context) -> None:
    _assert_problem(
        context.values["unauthorized"],
        status=403,
        code="publish_authorization_required",
    )
    _assert_problem(
        context.values["authorized"],
        status=409,
        code="invalid_episode_transition",
    )
