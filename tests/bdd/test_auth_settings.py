"""BDD coverage for fail-closed authentication runtime settings."""

from pytest_bdd import given, scenarios, then, when

from poddown.auth import AuthSettings

scenarios("../features/auth_settings.feature")


@given("no authentication mode is configured")
def no_authentication_mode(context) -> None:
    """Provide an environment without an authentication override."""
    context.values["environment"] = {}


@given("local authentication mode is configured")
def local_authentication_mode(context) -> None:
    """Provide the explicit local compatibility mode."""
    context.values["environment"] = {"PODDOWN_AUTH_MODE": "local"}


@when("authentication settings are parsed")
def parse_authentication_settings(context) -> None:
    """Parse the injected non-secret environment mapping."""
    context.values["settings"] = AuthSettings.from_environment(
        context.values["environment"]
    )


@when("an invalid authentication mode is constructed directly")
def construct_invalid_authentication_mode(context) -> None:
    """Capture direct construction so its validation remains observable."""
    try:
        AuthSettings(mode="invalid")  # type: ignore[arg-type]
    except ValueError as error:
        context.values["error"] = error


@then("authentication settings reject the invalid mode")
def authentication_settings_reject_invalid_mode(context) -> None:
    """Assert that typing cannot bypass runtime authentication validation."""
    assert str(context.values["error"]) == "mode must be local or oidc"


@then("OIDC authentication is required and headers cannot supply scope")
def oidc_authentication_is_required(context) -> None:
    """Assert the production-safe default authorization boundary."""
    settings = context.values["settings"]

    assert settings.mode == "oidc"
    assert settings.allows_header_compatibility is False


@then("local header compatibility is enabled")
def local_header_compatibility_is_enabled(context) -> None:
    """Assert compatibility is an explicit local-only choice."""
    settings = context.values["settings"]

    assert settings.mode == "local"
    assert settings.allows_header_compatibility is True
