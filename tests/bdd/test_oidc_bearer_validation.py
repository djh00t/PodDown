"""BDD coverage for the offline OIDC bearer-token boundary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from pytest_bdd import given, scenarios, then, when

from poddown.api.auth import AuthenticationError, BearerTokenValidator

scenarios("../features/oidc_bearer_validation.feature")

_ISSUER = "https://issuer.example.test"
_AUDIENCE = "poddown-api"
_KEY = "v" * 64


@given("an offline OIDC bearer-token validator")
def offline_validator(context) -> None:
    """Configure deterministic local verification with no network adapter."""
    context.values["validator"] = BearerTokenValidator(
        verification_key=_KEY,
        issuer=_ISSUER,
        audience=_AUDIENCE,
        algorithms=("HS256",),
    )


@given("a valid signed bearer token")
def valid_bearer_token(context) -> None:
    """Create a short-lived token signed by the injected verification key."""
    context.values["authorization"] = _authorization(_KEY)


@given("a bearer token signed by an untrusted key")
def invalid_signature_bearer_token(context) -> None:
    """Create a token whose signing key is outside configured authority."""
    context.values["authorization"] = _authorization("w" * 64)


@when("the bearer token is validated")
def validate_bearer_token(context) -> None:
    """Capture either verified claims or the stable authentication failure."""
    try:
        context.values["verified"] = context.values["validator"].validate(
            context.values["authorization"]
        )
    except AuthenticationError as error:
        context.values["error"] = error


@then("verified subject and issuer claims are returned")
def verified_claims_are_returned(context) -> None:
    """Assert principal input comes from the cryptographically verified token."""
    verified = context.values["verified"]
    assert verified.subject == "bdd-user"
    assert verified.issuer == _ISSUER


@then("a safe invalid bearer-token error is returned")
def safe_authentication_error_is_returned(context) -> None:
    """Assert signature details and token contents are not exposed."""
    error = context.values["error"]
    assert error.code == "invalid_bearer_token"
    assert str(error) == "Bearer token is invalid"
    assert context.values["authorization"] not in str(error)


def _authorization(key: str) -> str:
    """Return a local Bearer credential with required principal claims."""
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": "bdd-user",
            "iss": _ISSUER,
            "aud": _AUDIENCE,
            "exp": now + timedelta(minutes=5),
            "nbf": now - timedelta(seconds=1),
        },
        key,
        algorithm="HS256",
    )
    return f"Bearer {token}"
