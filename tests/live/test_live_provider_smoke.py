"""Explicitly opt-in one-segment live-provider smoke coverage."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pytest_bdd import given, scenarios, then, when

from poddown.audio.contracts import RenderRequest
from poddown.audio.render import DurableRenderService
from poddown.audio.storage import FilesystemArtifactStore, FilesystemRenderRecordStore
from poddown.providers.contracts import ProviderEvidence, provider_usage_to_mapping
from poddown.providers.runtime import LiveProviderRuntime
from poddown.qa.fidelity import evaluate_critical_tokens

pytestmark = pytest.mark.live_provider

scenarios("../features/live_provider_smoke.feature")


@pytest.fixture
def context() -> Any:
    """Provide the small scenario state object used by this live module."""
    return type("ScenarioContext", (), {"values": {}})()


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.fail(f"{name} is required for the live smoke")
    return value


def _critical_tokens() -> tuple[str, ...]:
    raw = _required_environment("PODDOWN_LIVE_SMOKE_CRITICAL_TOKENS_JSON")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        pytest.fail("PODDOWN_LIVE_SMOKE_CRITICAL_TOKENS_JSON is invalid")
        raise AssertionError from error
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        pytest.fail("live smoke critical tokens must be a non-empty string list")
    return tuple(value)


@given("a fully configured live-provider smoke environment")
def configured_live_smoke_environment(context: Any) -> None:
    """Require both the repository guard and the smoke-specific opt-in."""
    if os.environ.get("PODDOWN_LIVE_PROVIDER_TESTS") != "1":
        pytest.skip("live provider tests require PODDOWN_LIVE_PROVIDER_TESTS=1")
    if os.environ.get("PODDOWN_LIVE_SMOKE") != "1":
        pytest.skip("live smoke requires PODDOWN_LIVE_SMOKE=1")
    context.values["runtime"] = LiveProviderRuntime.from_environment(os.environ)


@when("one live segment is rendered and transcribed")
def render_and_transcribe_live_segment(context: Any, tmp_path: Path) -> None:
    runtime: LiveProviderRuntime = context.values["runtime"]
    source_voice_asset_id = _required_environment("PODDOWN_LIVE_SMOKE_VOICE_ASSET_ID")
    text = _required_environment("PODDOWN_LIVE_SMOKE_TEXT")
    expected_tokens = _critical_tokens()
    provider_voice_asset_id = runtime.render_binding.provider_voice_asset_id(
        source_voice_asset_id
    )
    try:
        consent = runtime.render_binding.consents[provider_voice_asset_id]
    except KeyError as error:
        pytest.fail("live smoke voice consent mapping is incomplete")
        raise AssertionError from error

    request = RenderRequest(
        episode_id=f"live-smoke-{uuid4()}",
        episode_version="live-smoke-v1",
        segment_id="live-smoke-segment",
        speaker_id="live-smoke-speaker",
        expected_spoken_text=text,
        voice_asset_id=provider_voice_asset_id,
        provider=runtime.render_binding.provider,
        model=runtime.render_binding.model,
    )
    service = DurableRenderService(
        FilesystemArtifactStore(tmp_path / "artifacts"),
        FilesystemRenderRecordStore(tmp_path / "records"),
    )
    outcome = asyncio.run(
        service.render_takes(
            request,
            consent,
            runtime.renderer,
            take_count=1,
        )
    )[0]
    audio = FilesystemArtifactStore(tmp_path / "artifacts").read(
        outcome.candidate.artifact
    )
    transcript = asyncio.run(runtime.transcriber.transcribe(audio))
    fidelity = evaluate_critical_tokens(expected_tokens, transcript.text)
    now = datetime.now(UTC)
    evidence = (
        ProviderEvidence(
            operation="render",
            provider=outcome.candidate.provider,
            request_id=outcome.candidate.request_id,
            model=outcome.candidate.model,
            input_sha256=sha256(text.encode("utf-8")).hexdigest(),
            output_sha256=outcome.candidate.artifact.sha256,
            usage=provider_usage_to_mapping(outcome.candidate.usage),
            currency="USD",
            estimated_cost=outcome.candidate.cost,
            reconciled_cost=None,
            latency_ms=0,
            retry_count=0,
            occurred_at=now,
            evidence_kind="provider-live",
        ),
        ProviderEvidence(
            operation="transcribe",
            provider=transcript.provider,
            request_id=transcript.request_id,
            model=transcript.model,
            input_sha256=transcript.checksum,
            output_sha256=sha256(transcript.text.encode("utf-8")).hexdigest(),
            usage=provider_usage_to_mapping(transcript.usage),
            currency="USD",
            estimated_cost=transcript.cost,
            reconciled_cost=None,
            latency_ms=0,
            retry_count=0,
            occurred_at=now,
            evidence_kind="provider-live",
        ),
    )
    context.values.update(
        outcome=outcome,
        transcript=transcript,
        fidelity=fidelity,
        evidence=evidence,
    )


@then("provider render and ASR evidence pass the critical-token gate")
def live_evidence_passes(context: Any) -> None:
    runtime: LiveProviderRuntime = context.values["runtime"]
    outcome = context.values["outcome"]
    transcript = context.values["transcript"]
    fidelity = context.values["fidelity"]
    evidence = context.values["evidence"]
    assert outcome.candidate.provider == runtime.settings.route.renderer.provider
    assert transcript.provider == runtime.settings.route.transcriber.provider
    assert transcript.mode != "deterministic-local"
    assert transcript.checksum == outcome.candidate.artifact.sha256
    assert fidelity.passed is True
    assert fidelity.accuracy == 1.0
    assert all(item.evidence_kind == "provider-live" for item in evidence)
    assert all(
        item.estimated_cost <= runtime.settings.route.max_request_cost
        for item in evidence
    )
    assert (
        sum(item.estimated_cost for item in evidence)
        <= runtime.settings.route.max_episode_cost
    )
