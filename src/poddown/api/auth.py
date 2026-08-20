"""Offline bearer-token authentication with injected JWT verification policy."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, cast

import jwt

AuthenticationErrorCode = Literal[
    "missing_bearer_token",
    "invalid_bearer_token",
]

_BEARER_CREDENTIAL = re.compile(r"Bearer +([^\s]+)", flags=re.IGNORECASE)


class AuthenticationError(Exception):
    """A stable, credential-safe authentication failure for API mapping."""

    def __init__(self, code: AuthenticationErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class VerifiedTokenClaims:
    """Principal input sourced only from a cryptographically verified token."""

    subject: str
    issuer: str
    claims: Mapping[str, object]


class BearerTokenValidator:
    """Validate JWT bearer tokens using only injected offline authority."""

    def __init__(
        self,
        *,
        verification_key: str | bytes,
        issuer: str,
        audience: str,
        algorithms: tuple[str, ...],
    ) -> None:
        """Configure a static verification key and explicit claim policy."""
        if not verification_key:
            raise ValueError("verification_key must not be empty")
        if not issuer.strip():
            raise ValueError("issuer must not be blank")
        if not audience.strip():
            raise ValueError("audience must not be blank")
        if not algorithms or any(
            not algorithm.strip() or algorithm.casefold() == "none"
            for algorithm in algorithms
        ):
            raise ValueError("algorithms must contain allowed signed algorithms")

        self._verification_key = verification_key
        self._issuer = issuer
        self._audience = audience
        self._algorithms = algorithms

    def validate(self, authorization: str | None) -> VerifiedTokenClaims:
        """Return verified claims or raise a safe fail-closed error."""
        token = self._parse_bearer_credential(authorization)
        try:
            decoded = jwt.decode(
                token,
                self._verification_key,
                algorithms=list(self._algorithms),
                audience=self._audience,
                issuer=self._issuer,
                options={
                    "require": ["sub", "iss", "aud", "exp"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iss": True,
                    "verify_aud": True,
                    "verify_sub": True,
                },
            )
        except jwt.PyJWTError:
            raise AuthenticationError(
                "invalid_bearer_token",
                "Bearer token is invalid",
            ) from None

        claims = cast(dict[str, object], decoded)
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            raise AuthenticationError(
                "invalid_bearer_token",
                "Bearer token is invalid",
            )
        return VerifiedTokenClaims(
            subject=subject,
            issuer=self._issuer,
            claims=MappingProxyType(dict(claims)),
        )

    @staticmethod
    def _parse_bearer_credential(authorization: str | None) -> str:
        """Extract one Bearer credential without accepting another scheme."""
        if authorization is None or not authorization.strip():
            raise AuthenticationError(
                "missing_bearer_token",
                "Bearer token is required",
            )
        matched = _BEARER_CREDENTIAL.fullmatch(authorization)
        if matched is None:
            raise AuthenticationError(
                "invalid_bearer_token",
                "Bearer token is invalid",
            )
        return matched.group(1)


__all__ = [
    "AuthenticationError",
    "AuthenticationErrorCode",
    "BearerTokenValidator",
    "VerifiedTokenClaims",
]
