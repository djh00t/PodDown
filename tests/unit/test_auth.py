"""Unit contracts for authentication settings and principal scope."""

from __future__ import annotations

import os
from uuid import UUID

import pytest

from poddown.auth import AuthenticationError, AuthSettings, LocalHeaderPrincipalResolver


def test_api_settings_require_verified_oidc_configuration() -> None:
    with pytest.raises(ValueError, match="issuer"):
        AuthSettings(mode="api")


def test_environment_defaults_to_api_and_requires_explicit_local_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PODDOWN_AUTH_MODE", raising=False)
    monkeypatch.delenv("PODDOWN_OIDC_ISSUER", raising=False)
    monkeypatch.delenv("PODDOWN_OIDC_AUDIENCE", raising=False)
    monkeypatch.delenv("PODDOWN_OIDC_HS256_KEY", raising=False)
    with pytest.raises(ValueError, match="OIDC"):
        AuthSettings.from_environment()

    monkeypatch.setenv("PODDOWN_AUTH_MODE", "local")
    settings = AuthSettings.from_environment()
    assert settings.mode == "local"


def test_local_resolver_rejects_non_local_settings() -> None:
    with pytest.raises(ValueError, match="local"):
        LocalHeaderPrincipalResolver(
            AuthSettings(
                mode="api",
                issuer="https://issuer.example.test/",
                audience="poddown-api",
                verification_key=os.urandom(32),
            )
        )


def test_local_resolver_rejects_non_uuidv7_scope() -> None:
    resolver = LocalHeaderPrincipalResolver(AuthSettings(mode="local"))
    with pytest.raises(AuthenticationError):
        resolver.resolve(
            tenant_header="00000000-0000-4000-8000-000000000000",
            project_header=str(UUID("018f2c8b-7b46-7cc5-b2e1-222222222222")),
        )
