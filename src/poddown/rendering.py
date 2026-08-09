"""Provider-independent rights, capability, and dispatch policy."""

import asyncio
import hashlib
from collections.abc import Collection
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

from poddown.domain import CandidateResult, ProviderUsage
from poddown.providers.contracts import ProviderCapabilities, VoiceRenderer

_HARD_GATES = frozenset({"audio_quality", "critical_tokens"})
_DEFAULT_CAPABILITIES = ProviderCapabilities(
    formats=frozenset({"wav"}),
    sample_rates=frozenset({44_100}),
    max_text_characters=10_000,
    model_pinning=True,
    voice_pinning=True,
    timestamps=False,
    provider_idempotency=True,
)


class _DeterministicRenderer:
    """Offline renderer used to establish policy before vendor adapters."""

    capabilities = _DEFAULT_CAPABILITIES

    def __init__(self, provider: str):
        self.provider = provider

    async def render(self, text: str) -> CandidateResult:
        candidate_id = str(uuid4())
        audio = f"{self.provider}:{candidate_id}:{text}".encode()
        return CandidateResult(
            accepted=True,
            provider_calls=1,
            provider=self.provider,
            candidate_id=candidate_id,
            submitted_text=text,
            request_id=f"request-{candidate_id}",
            model=f"{self.provider}-test-model",
            usage=ProviderUsage(len(text), len(audio)),
            cost=Decimal("0"),
            checksum=hashlib.sha256(audio).hexdigest(),
        )


def _rejected(provider_calls: int = 0) -> CandidateResult:
    return CandidateResult(False, provider_calls, required_gates=_HARD_GATES)


def _supports_request(capabilities: ProviderCapabilities, text: str) -> bool:
    return (
        bool(capabilities.formats)
        and bool(capabilities.sample_rates)
        and capabilities.max_text_characters >= len(text)
        and capabilities.model_pinning
        and capabilities.voice_pinning
    )


def _valid_metadata(result: CandidateResult, provider: str, text: str) -> bool:
    usage_valid = bool(
        result.usage
        and result.usage.input_units >= 0
        and result.usage.output_units >= 0
    )
    checksum_valid = bool(
        result.checksum
        and len(result.checksum) == 64
        and all(character in "0123456789abcdef" for character in result.checksum)
    )
    return (
        result.accepted
        and result.provider_calls == 1
        and result.provider == provider
        and result.submitted_text == text
        and all(
            (
                result.candidate_id,
                result.request_id,
                result.model,
            )
        )
        and usage_valid
        and checksum_valid
        and result.cost.is_finite()
        and result.cost >= 0
    )


async def request_candidate_async(
    text: str,
    rights_valid: bool,
    provider: str,
    eligible: Collection[str],
    unavailable: Collection[str],
    renderer: VoiceRenderer | None = None,
) -> CandidateResult:
    """Apply hard policy in order, then dispatch exactly one provider call."""
    if not rights_valid or provider not in eligible or provider in unavailable:
        return _rejected()
    selected = renderer or _DeterministicRenderer(provider)
    if not _supports_request(selected.capabilities, text):
        return _rejected()

    result = await selected.render(text)
    if not _valid_metadata(result, provider, text):
        return _rejected(provider_calls=result.provider_calls)
    return replace(result, required_gates=_HARD_GATES)


def request_candidate(
    text: str,
    rights_valid: bool,
    provider: str,
    eligible: Collection[str],
    unavailable: Collection[str],
    renderer: VoiceRenderer | None = None,
) -> CandidateResult:
    """Synchronous facade for CLI and current BDD use."""
    return asyncio.run(
        request_candidate_async(
            text, rights_valid, provider, eligible, unavailable, renderer
        )
    )
