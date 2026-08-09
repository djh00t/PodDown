"""Tests for provider selection and pre-dispatch policy."""

from decimal import Decimal

import pytest

from poddown.domain import CandidateResult, ProviderUsage
from poddown.providers.contracts import ProviderCapabilities
from poddown.rendering import request_candidate


class RecordingRenderer:
    """Deterministic renderer that records immutable application input."""

    def __init__(self, *, max_text_characters=1000, mutate_text=False):
        self.calls = []
        self.mutate_text = mutate_text
        self.capabilities = ProviderCapabilities(
            formats=frozenset({"wav"}),
            sample_rates=frozenset({44_100}),
            max_text_characters=max_text_characters,
            model_pinning=True,
            voice_pinning=True,
            timestamps=False,
            provider_idempotency=True,
        )

    async def render(self, text):
        self.calls.append(text)
        submitted = text + " rewritten" if self.mutate_text else text
        return CandidateResult(
            accepted=True,
            provider_calls=1,
            provider="elevenlabs",
            candidate_id="candidate-1",
            submitted_text=submitted,
            request_id="request-1",
            model="eleven-multilingual-v2",
            usage=ProviderUsage(input_units=len(text), output_units=8),
            cost=Decimal("0.03"),
            checksum="a" * 64,
            required_gates=frozenset(),
        )


@pytest.mark.parametrize(
    ("rights_valid", "eligible", "unavailable"),
    [
        (False, {"elevenlabs"}, set()),
        (True, set(), set()),
        (True, {"elevenlabs"}, {"elevenlabs"}),
    ],
)
def test_policy_rejection_happens_before_dispatch(rights_valid, eligible, unavailable):
    """Rights, eligibility, and availability failures incur no provider call."""
    renderer = RecordingRenderer()

    result = request_candidate(
        text="canonical text",
        rights_valid=rights_valid,
        provider="elevenlabs",
        eligible=eligible,
        unavailable=unavailable,
        renderer=renderer,
    )

    assert result.accepted is False
    assert result.provider_calls == 0
    assert renderer.calls == []


def test_unsupported_text_length_is_rejected_before_dispatch():
    """Provider capability limits cannot be discovered through paid failures."""
    renderer = RecordingRenderer(max_text_characters=4)

    result = request_candidate(
        "too long", True, "elevenlabs", {"elevenlabs"}, set(), renderer
    )

    assert result.accepted is False
    assert renderer.calls == []


def test_provider_cannot_mutate_canonical_submitted_text():
    """A renderer response claiming rewritten input is rejected."""
    renderer = RecordingRenderer(mutate_text=True)

    result = request_candidate(
        "canonical", True, "elevenlabs", {"elevenlabs"}, set(), renderer
    )

    assert renderer.calls == ["canonical"]
    assert result.accepted is False


def test_success_normalizes_metadata_and_restores_hard_gates():
    """A valid provider result retains metering and mandatory downstream gates."""
    renderer = RecordingRenderer()

    result = request_candidate(
        "canonical", True, "elevenlabs", {"elevenlabs"}, set(), renderer
    )

    assert result.accepted is True
    assert result.submitted_text == "canonical"
    assert result.usage == ProviderUsage(9, 8)
    assert result.cost == Decimal("0.03")
    assert result.required_gates == frozenset({"critical_tokens", "audio_quality"})


def test_separate_requests_receive_distinct_default_candidate_ids():
    """Two billed takes cannot collapse into one candidate record."""
    arguments = ("canonical", True, "openai", {"openai"}, set())

    first = request_candidate(*arguments)
    second = request_candidate(*arguments)

    assert first.candidate_id != second.candidate_id
    assert first.provider == second.provider == "openai"


@pytest.mark.parametrize(
    ("usage", "cost", "checksum"),
    [
        (ProviderUsage(-1, 1), Decimal("0"), "a" * 64),
        (ProviderUsage(1, 1), Decimal("NaN"), "a" * 64),
        (ProviderUsage(1, 1), Decimal("0"), "not-a-checksum"),
    ],
)
def test_malformed_normalized_metadata_is_rejected(usage, cost, checksum):
    renderer = RecordingRenderer()
    renderer.result = None

    async def malformed(text):
        candidate = await RecordingRenderer.render(renderer, text)
        return CandidateResult(
            **{
                **candidate.__dict__,
                "usage": usage,
                "cost": cost,
                "checksum": checksum,
            }
        )

    renderer.render = malformed
    result = request_candidate(
        "canonical", True, "elevenlabs", {"elevenlabs"}, set(), renderer
    )

    assert result.accepted is False
