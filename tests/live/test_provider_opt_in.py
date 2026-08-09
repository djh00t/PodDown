"""Harmless proof of the two-part live-provider execution guard."""

import os

import pytest

from poddown.providers.testing import live_provider_enabled


@pytest.mark.live_provider
@pytest.mark.skipif(
    not live_provider_enabled(os.environ),
    reason="live provider tests require PODDOWN_LIVE_PROVIDER_TESTS=1",
)
def test_live_provider_marker_and_environment_opt_in_are_both_present():
    """Reach live-provider code only with both marker selection and env opt-in."""
    assert os.environ["PODDOWN_LIVE_PROVIDER_TESTS"] == "1"
