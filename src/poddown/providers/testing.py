"""Opt-in policy for provider tests that consume live APIs."""

from collections.abc import Mapping


def live_provider_enabled(environment: Mapping[str, str]) -> bool:
    """Return whether live provider execution was explicitly enabled."""
    return environment.get("PODDOWN_LIVE_PROVIDER_TESTS") == "1"
