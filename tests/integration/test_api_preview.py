from __future__ import annotations

from hashlib import sha256

from fastapi.testclient import TestClient

from poddown.api import create_app

TENANT_ID = "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10"
PROJECT_ID = "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12"
SOURCE = "---\npoddown:\n  profile: technical-dialogue\n---\n# API preview\n"


def _headers(key: str) -> dict[str, str]:
    return {
        "X-Tenant-ID": TENANT_ID,
        "X-Project-ID": PROJECT_ID,
        "Idempotency-Key": key,
    }


def test_preview_route_returns_source_bound_provider_free_result() -> None:
    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/preview",
            headers=_headers("preview-api-001"),
            json={"source": SOURCE, "profile": "technical-dialogue"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "source_sha256": sha256(SOURCE.encode("utf-8")).hexdigest(),
        "profile_id": "technical-dialogue",
        "source_bytes": len(SOURCE.encode("utf-8")),
        "block_count": 1,
        "provider_calls": 0,
        "side_effect": "none",
    }


def test_preview_route_rejects_unknown_profile_without_echoing_source() -> None:
    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/preview",
            headers=_headers("preview-api-002"),
            json={"source": SOURCE, "profile": "unknown-profile"},
        )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "invalid_profile"
    assert SOURCE not in response.text
