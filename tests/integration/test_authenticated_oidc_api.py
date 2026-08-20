"""Local authenticated API integration using the real OIDC JWT verifier."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from fastapi.testclient import TestClient

from poddown.api import create_app
from poddown.auth import AuthSettings

TENANT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")
KEY = b"local-oidc-integration-key-v1-test"


def _token(*, key: bytes = KEY, subject: str = "uat-user") -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": "https://issuer.example.test/",
            "sub": subject,
            "aud": "poddown-api",
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "tenant_id": str(TENANT),
            "project_ids": [str(PROJECT)],
            "scope": "episodes:read episodes:write",
        },
        key,
        algorithm="HS256",
    )


def _settings() -> AuthSettings:
    return AuthSettings(
        mode="api",
        issuer="https://issuer.example.test/",
        audience="poddown-api",
        verification_key=KEY,
    )


def test_real_oidc_token_scopes_api_creation_without_compatibility_headers() -> None:
    with TestClient(create_app(auth_settings=_settings())) as client:
        response = client.post(
            "/v1/episodes",
            headers={
                "Authorization": f"Bearer {_token()}",
                "Idempotency-Key": "real-oidc-api-001",
            },
            json={
                "source": "# Authenticated local integration\n",
                "profile": "default",
            },
        )
    assert response.status_code == 202
    assert response.json()["episode"]["tenant_id"] == str(TENANT)
    assert response.json()["episode"]["project_id"] == str(PROJECT)


def test_real_oidc_signature_failure_is_rejected_before_episode_creation() -> None:
    invalid_token = _token(key=b"wrong-local-oidc-key-v1-test-32-byte")
    with TestClient(create_app(auth_settings=_settings())) as client:
        response = client.post(
            "/v1/episodes",
            headers={
                "Authorization": f"Bearer {invalid_token}",
                "Idempotency-Key": "real-oidc-api-002",
            },
            json={"source": "# Must not be accepted\n", "profile": "default"},
        )
    assert response.status_code == 401
    assert response.json()["code"] == "authentication_failed"
