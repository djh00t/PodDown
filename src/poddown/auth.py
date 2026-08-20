"""Fail-closed OIDC principal verification and local compatibility scope."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol, cast
from uuid import UUID

import jwt

AuthMode = Literal["api", "local", "oidc"]
_LOCAL_SCOPES = frozenset(
    {
        "episodes:read",
        "episodes:write",
        "episodes:render",
        "episodes:publish",
        "resources:read",
    }
)


class AuthenticationError(ValueError):
    """Stable safe authentication failure with no token or claim details."""

    def __init__(self, code: str = "authentication_failed") -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class AuthSettings:
    """Runtime authentication configuration; signing material is never serialized."""

    mode: AuthMode
    issuer: str | None = None
    audience: str | None = None
    algorithms: tuple[str, ...] = ("HS256",)
    verification_key: str | bytes | None = field(
        default=None, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if self.mode not in {"api", "local", "oidc"}:
            raise ValueError("mode must be local or oidc")
        if self.mode == "api":
            if not isinstance(self.issuer, str) or not self.issuer.strip():
                raise ValueError("OIDC issuer is required in api mode")
            if not isinstance(self.audience, str) or not self.audience.strip():
                raise ValueError("OIDC audience is required in api mode")
            if not self.algorithms or any(
                not isinstance(algorithm, str) or not algorithm.strip()
                for algorithm in self.algorithms
            ):
                raise ValueError("OIDC algorithms are required in api mode")
            if self.verification_key is None or (
                isinstance(self.verification_key, str)
                and not self.verification_key.strip()
            ):
                raise ValueError("OIDC verification key is required in api mode")
        elif self.issuer is not None or self.audience is not None:
            raise ValueError(
                "local or compatibility OIDC mode cannot carry OIDC metadata"
            )

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> AuthSettings:
        """Load runtime settings from process or explicit compatibility input.

        The process environment retains the production API/OIDC boundary.
        An injected mapping is the historical local/OIDC settings contract and
        defaults to OIDC without consulting ambient process state.
        """
        if environment is not None:
            raw_mode = environment.get("PODDOWN_AUTH_MODE", "oidc").strip().lower()
            if raw_mode not in {"local", "oidc"}:
                raise ValueError("PODDOWN_AUTH_MODE must be local or oidc")
            return cls(mode=cast(AuthMode, raw_mode))

        raw_mode = os.environ.get("PODDOWN_AUTH_MODE", "api").strip().lower()
        if raw_mode not in {"api", "local"}:
            raise ValueError("PODDOWN_AUTH_MODE must be api or local")
        if raw_mode == "local":
            return cls(mode="local")
        issuer = os.environ.get("PODDOWN_OIDC_ISSUER", "").strip()
        audience = os.environ.get("PODDOWN_OIDC_AUDIENCE", "").strip()
        key = os.environ.get("PODDOWN_OIDC_HS256_KEY", "")
        return cls(
            mode="api",
            issuer=issuer,
            audience=audience,
            verification_key=key,
        )

    @property
    def allows_header_compatibility(self) -> bool:
        """Return whether explicit local header compatibility is available."""
        return self.mode == "local"

    def to_record(self) -> dict[str, object]:
        """Return safe configuration metadata without signing material."""
        return {
            "mode": self.mode,
            "issuer": self.issuer,
            "audience": self.audience,
            "algorithms": list(self.algorithms),
            "verification_key_configured": self.verification_key is not None,
        }


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """Verified identity and tenant/project authorization scope."""

    issuer: str
    subject: str
    tenant_id: UUID
    project_ids: frozenset[UUID]
    scopes: frozenset[str]
    claims: Mapping[str, object] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.issuer.strip() or not self.subject.strip():
            raise AuthenticationError("principal_identity_invalid")
        if self.tenant_id.version != 7:
            raise AuthenticationError("principal_tenant_invalid")
        if not self.project_ids or any(
            not isinstance(project_id, UUID) or project_id.version != 7
            for project_id in self.project_ids
        ):
            raise AuthenticationError("principal_project_scope_invalid")
        if any(
            not isinstance(scope, str) or not scope.strip() for scope in self.scopes
        ):
            raise AuthenticationError("principal_scope_invalid")
        if not isinstance(self.claims, Mapping):
            raise AuthenticationError("principal_claims_invalid")
        object.__setattr__(self, "claims", _freeze_claims(self.claims))

    def allows_project(self, project_id: UUID) -> bool:
        """Return whether this principal may address one project."""
        return project_id in self.project_ids

    def has_scope(self, scope: str) -> bool:
        """Return whether the principal carries one exact or local wildcard scope."""
        return scope in self.scopes or "*" in self.scopes


class PrincipalVerifier(Protocol):
    """Port for verified request-principal construction."""

    def verify(self, authorization_header: str) -> AuthenticatedPrincipal:
        """Verify an Authorization header and return a trusted principal."""


KeyResolver = Callable[[Mapping[str, object]], str | bytes]


def _freeze_claims(claims: Mapping[str, object]) -> Mapping[str, object]:
    """Copy claims while keeping the principal immutable and source-safe."""
    return {str(key): value for key, value in claims.items()}


def _parse_uuid7(value: object, code: str) -> UUID:
    try:
        parsed = value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError) as error:
        raise AuthenticationError(code) from error
    if parsed.version != 7:
        raise AuthenticationError(code)
    return parsed


def _claim_scopes(claims: Mapping[str, object]) -> frozenset[str]:
    scope = claims.get("scope")
    if isinstance(scope, str):
        return frozenset(value for value in scope.split() if value)
    scopes = claims.get("scopes")
    if isinstance(scopes, list) and all(isinstance(value, str) for value in scopes):
        return frozenset(value for value in scopes if value)
    raise AuthenticationError("principal_scope_invalid")


def _claim_projects(claims: Mapping[str, object]) -> frozenset[UUID]:
    values = claims.get("project_ids")
    if values is None and claims.get("project_id") is not None:
        values = [claims["project_id"]]
    if not isinstance(values, list) or not values:
        raise AuthenticationError("principal_project_scope_invalid")
    return frozenset(
        _parse_uuid7(value, "principal_project_invalid") for value in values
    )


def _principal_from_claims(claims: Mapping[str, object]) -> AuthenticatedPrincipal:
    issuer = claims.get("iss")
    subject = claims.get("sub")
    if not isinstance(issuer, str) or not isinstance(subject, str):
        raise AuthenticationError("principal_identity_invalid")
    return AuthenticatedPrincipal(
        issuer=issuer,
        subject=subject,
        tenant_id=_parse_uuid7(claims.get("tenant_id"), "principal_tenant_invalid"),
        project_ids=_claim_projects(claims),
        scopes=_claim_scopes(claims),
        claims=claims,
    )


class OIDCPrincipalVerifier:
    """Verify signed OIDC JWTs with issuer, audience and algorithm pinning."""

    def __init__(
        self,
        settings: AuthSettings,
        *,
        key_resolver: KeyResolver | None = None,
    ) -> None:
        if settings.mode != "api":
            raise ValueError("OIDC verifier requires api auth mode")
        self._settings = settings
        self._key_resolver = key_resolver

    def verify(self, authorization_header: str) -> AuthenticatedPrincipal:
        """Verify one bearer token without exposing parser or provider errors."""
        token = self._bearer_token(authorization_header)
        try:
            header = cast(dict[str, object], jwt.get_unverified_header(token))
            algorithm = header.get("alg")
            if (
                not isinstance(algorithm, str)
                or algorithm not in self._settings.algorithms
            ):
                raise AuthenticationError("token_algorithm_rejected")
            key = (
                self._key_resolver(header)
                if self._key_resolver is not None
                else self._settings.verification_key
            )
            if key is None:
                raise AuthenticationError("oidc_key_unavailable")
            claims = jwt.decode(
                token,
                key,
                algorithms=list(self._settings.algorithms),
                audience=self._settings.audience,
                issuer=self._settings.issuer,
                options={
                    "require": ["exp", "iat", "iss", "sub", "aud"],
                },
            )
            if not isinstance(claims, Mapping):
                raise AuthenticationError("principal_claims_invalid")
            return _principal_from_claims(cast(Mapping[str, object], claims))
        except AuthenticationError:
            raise
        except (jwt.InvalidTokenError, TypeError, ValueError) as error:
            raise AuthenticationError() from error

    @staticmethod
    def _bearer_token(value: str) -> str:
        if not isinstance(value, str):
            raise AuthenticationError("authorization_missing")
        scheme, separator, token = value.partition(" ")
        if scheme.casefold() != "bearer" or not separator or not token.strip():
            raise AuthenticationError("authorization_invalid")
        return token.strip()


class LocalHeaderPrincipalResolver:
    """Explicit local-only compatibility resolver for tenant/project headers."""

    def __init__(self, settings: AuthSettings) -> None:
        if settings.mode != "local":
            raise ValueError("local header resolver requires local auth mode")

    def resolve(
        self,
        *,
        tenant_header: str | None,
        project_header: str | None,
    ) -> AuthenticatedPrincipal:
        """Create a local principal only from validated compatibility headers."""
        if tenant_header is None or project_header is None:
            raise AuthenticationError("authorization_missing")
        tenant_id = _parse_uuid7(tenant_header, "principal_tenant_invalid")
        project_id = _parse_uuid7(project_header, "principal_project_invalid")
        return AuthenticatedPrincipal(
            issuer="local",
            subject="local-header",
            tenant_id=tenant_id,
            project_ids=frozenset({project_id}),
            scopes=_LOCAL_SCOPES,
            claims={"mode": "local"},
        )


__all__ = [
    "AuthMode",
    "AuthSettings",
    "AuthenticatedPrincipal",
    "AuthenticationError",
    "KeyResolver",
    "LocalHeaderPrincipalResolver",
    "OIDCPrincipalVerifier",
    "PrincipalVerifier",
]
