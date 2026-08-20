"""Unit coverage for offline OIDC bearer-token verification."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from poddown.api.auth import AuthenticationError, BearerTokenValidator

ISSUER = "https://issuer.example.test"
AUDIENCE = "poddown-api"
VERIFICATION_KEY = "v" * 64
WRONG_KEY = "w" * 64


def _claims(**overrides: object) -> dict[str, object]:
    """Build independently controlled claims around a current UTC instant."""
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": "user-123",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": now + timedelta(minutes=5),
        "nbf": now - timedelta(seconds=1),
        "tenant_id": "verified-token-tenant",
    }
    claims.update(overrides)
    return claims


def _token(
    *,
    claims: dict[str, object] | None = None,
    key: str = VERIFICATION_KEY,
    algorithm: str = "HS256",
) -> str:
    """Encode a local test token without contacting an identity provider."""
    return jwt.encode(claims or _claims(), key, algorithm=algorithm)


def _authorization(
    *,
    claims: dict[str, object] | None = None,
    key: str = VERIFICATION_KEY,
    algorithm: str = "HS256",
) -> str:
    """Wrap one locally encoded token in the HTTP authentication scheme."""
    return f"Bearer {_token(claims=claims, key=key, algorithm=algorithm)}"


@pytest.fixture
def validator() -> BearerTokenValidator:
    """Return a validator with one injected key and one allowed algorithm."""
    return BearerTokenValidator(
        verification_key=VERIFICATION_KEY,
        issuer=ISSUER,
        audience=AUDIENCE,
        algorithms=("HS256",),
    )


def test_valid_bearer_token_returns_verified_claims(
    validator: BearerTokenValidator,
) -> None:
    """A correctly signed token produces principal input from claims only."""
    verified = validator.validate(f"Bearer {_token()}")

    assert verified.subject == "user-123"
    assert verified.issuer == ISSUER
    assert verified.claims["tenant_id"] == "verified-token-tenant"


@pytest.mark.parametrize(
    ("authorization", "expected_code"),
    [
        (None, "missing_bearer_token"),
        ("", "missing_bearer_token"),
        ("Basic abc", "invalid_bearer_token"),
        ("Bearer", "invalid_bearer_token"),
        ("Bearer token with spaces", "invalid_bearer_token"),
    ],
)
def test_missing_or_malformed_bearer_header_is_rejected(
    validator: BearerTokenValidator,
    authorization: str | None,
    expected_code: str,
) -> None:
    """Only one non-empty Bearer credential reaches JWT decoding."""
    with pytest.raises(AuthenticationError) as captured:
        validator.validate(authorization)

    assert captured.value.code == expected_code


@pytest.mark.parametrize(
    "authorization",
    [
        _authorization(key=WRONG_KEY),
        _authorization(claims=_claims(iss="https://wrong.example.test")),
        _authorization(claims=_claims(aud="wrong-audience")),
        _authorization(claims=_claims(exp=datetime.now(UTC) - timedelta(seconds=1))),
        _authorization(claims=_claims(nbf=datetime.now(UTC) + timedelta(minutes=5))),
        _authorization(algorithm="HS384"),
        f"Bearer {jwt.encode(_claims(), key='', algorithm='none')}",
        "Bearer not-a-jwt",
    ],
    ids=[
        "invalid-signature",
        "wrong-issuer",
        "wrong-audience",
        "expired",
        "not-before",
        "disallowed-algorithm",
        "none-algorithm",
        "malformed-token",
    ],
)
def test_invalid_tokens_map_to_one_safe_authentication_error(
    validator: BearerTokenValidator,
    authorization: str,
) -> None:
    """JWT failures expose neither decoder details nor credential material."""
    with pytest.raises(AuthenticationError) as captured:
        validator.validate(authorization)

    assert captured.value.code == "invalid_bearer_token"
    assert str(captured.value) == "Bearer token is invalid"
    assert authorization not in str(captured.value)
    assert VERIFICATION_KEY not in str(captured.value)


def test_token_without_expiry_is_rejected(
    validator: BearerTokenValidator,
) -> None:
    """A bearer token cannot create an unbounded authenticated session."""
    claims = _claims()
    del claims["exp"]

    with pytest.raises(AuthenticationError) as captured:
        validator.validate(f"Bearer {_token(claims=claims)}")

    assert captured.value.code == "invalid_bearer_token"
