"""Executable evals for invariant provider and CI policy."""

from dataclasses import replace
from decimal import Decimal

import pytest

from poddown.domain import CandidateResult, ProviderUsage
from poddown.providers.testing import live_provider_enabled
from poddown.rendering import request_candidate


class CountingRenderer:
    """Renderer spy proving pre-dispatch policy ordering."""

    from poddown.providers.contracts import ProviderCapabilities

    capabilities = ProviderCapabilities(
        formats=frozenset({"wav"}),
        sample_rates=frozenset({44_100}),
        max_text_characters=1_000,
        model_pinning=True,
        voice_pinning=True,
        timestamps=False,
        provider_idempotency=True,
    )

    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def render(self, text):
        self.calls += 1
        return replace(self.result, submitted_text=text)


@pytest.fixture
def valid_candidate():
    """Return complete normalized metadata accepted by dispatch policy."""
    return CandidateResult(
        accepted=True,
        provider_calls=1,
        provider="openai",
        candidate_id="candidate-1",
        submitted_text="Exact text",
        request_id="request-1",
        model="model-1",
        usage=ProviderUsage(10, 20),
        cost=Decimal("0.01"),
        checksum="a" * 64,
    )


def test_rights_rejection_precedes_dispatch_and_cost():
    renderer = CountingRenderer(CandidateResult(True, 1, provider="openai"))

    result = request_candidate(
        "Billable text", False, "openai", {"openai"}, set(), renderer
    )

    assert renderer.calls == 0
    assert result.provider_calls == 0
    assert result.cost == 0


def test_provider_result_cannot_waive_hard_gates(valid_candidate):
    renderer = CountingRenderer(replace(valid_candidate, required_gates=frozenset()))

    result = request_candidate(
        "Exact text", True, "openai", {"openai"}, set(), renderer
    )

    assert result.required_gates == frozenset({"critical_tokens", "audio_quality"})


def test_fallback_candidate_preserves_text_and_gates(valid_candidate):
    fallback = replace(valid_candidate, provider="openai", submitted_text="Exact text")
    result = request_candidate(
        "Exact text",
        True,
        "openai",
        {"openai", "elevenlabs"},
        {"elevenlabs"},
        CountingRenderer(fallback),
    )

    assert result.submitted_text == "Exact text"
    assert result.required_gates == frozenset({"critical_tokens", "audio_quality"})


def test_live_provider_tests_require_explicit_environment_opt_in():
    assert not live_provider_enabled({})
    assert not live_provider_enabled({"PODDOWN_LIVE_PROVIDER_TESTS": "0"})
    assert live_provider_enabled({"PODDOWN_LIVE_PROVIDER_TESTS": "1"})
