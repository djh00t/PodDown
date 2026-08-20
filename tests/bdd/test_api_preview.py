from __future__ import annotations

from hashlib import sha256
from typing import Any

from fastapi.testclient import TestClient
from pytest_bdd import given, scenarios, then, when

from poddown.api import create_app

scenarios("../features/api_preview.feature")

TENANT_ID = "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10"
PROJECT_ID = "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12"
SOURCE = "---\npoddown:\n  profile: technical-dialogue\n---\n# API preview\n"


def _headers(key: str) -> dict[str, str]:
    return {
        "X-Tenant-ID": TENANT_ID,
        "X-Project-ID": PROJECT_ID,
        "Idempotency-Key": key,
    }


@given("an offline episode API client for preview")
def preview_client(context: Any) -> None:
    context.values["client"] = TestClient(create_app())


@when("I submit valid Markdown to the preview route")
def submit_preview(context: Any) -> None:
    context.values["source"] = SOURCE
    context.values["response"] = context.values["client"].post(
        "/v1/preview",
        headers=_headers("preview-bdd-001"),
        json={"source": SOURCE, "profile": "technical-dialogue"},
    )


@then("the preview response preserves the source digest and reports no side effect")
def assert_preview(context: Any) -> None:
    response = context.values["response"]
    assert response.status_code == 200
    body = response.json()
    assert body["source_sha256"] == sha256(SOURCE.encode("utf-8")).hexdigest()
    assert body["profile_id"] == "technical-dialogue"
    assert body["provider_calls"] == 0
    assert body["side_effect"] == "none"


@when("I submit an unknown profile to the preview route")
def submit_unknown_profile(context: Any) -> None:
    context.values["response"] = context.values["client"].post(
        "/v1/preview",
        headers=_headers("preview-bdd-002"),
        json={"source": SOURCE, "profile": "unknown-profile"},
    )


@then("the preview response is a stable invalid-profile problem")
def assert_invalid_profile(context: Any) -> None:
    response = context.values["response"]
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_profile"
    assert SOURCE not in response.text
