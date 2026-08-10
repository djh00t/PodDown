"""Executable acceptance tests for durable provider-bound render activities."""

import asyncio
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
from typing import Any

import pytest
from pytest_bdd import given, scenarios, then, when
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from poddown.audio import (
    DeterministicLocalRenderer,
    DurableRenderService,
    EpisodeRenderWorkflow,
    EpisodeWorkflowInput,
    RenderRequest,
    SegmentWorkflowInput,
    TemporalEpisodeWorkflowService,
    VoiceConsent,
    activity_key_for,
    build_durable_render_activity,
)
from poddown.audio.selection import CandidateQuality
from poddown.audio.storage import (
    FilesystemArtifactStore,
    FilesystemQualityRecordStore,
    FilesystemRenderRecordStore,
    FilesystemTranscriptionRecordStore,
)
from poddown.audio.workflow import RENDER_SEGMENT_ACTIVITY_NAME
from poddown.domain import ProviderUsage
from poddown.providers.contracts import TranscriptResult

scenarios("../features/provider_temporal_render.feature")


class MatchingFakeTranscriber:
    """Return a matching transcript and count dispatches without network use."""

    def __init__(self, text: str):
        self.text = text
        self.calls = 0

    async def transcribe(self, audio: bytes) -> TranscriptResult:
        assert audio
        self.calls += 1
        return TranscriptResult(
            text=self.text,
            words=(),
            provider="fake-transcriber",
            model="fake-model-v1",
            usage=ProviderUsage(1, 1),
            request_id="fake-request-1",
            checksum=sha256(audio).hexdigest(),
            cost=Decimal("0.01"),
        )


class MalformedNonRetryableFakeTranscriber:
    """Simulate a malformed terminal provider response."""

    def __init__(self):
        self.calls = 0

    async def transcribe(self, audio: bytes) -> TranscriptResult:
        assert audio
        self.calls += 1
        raise ValueError("malformed transcription response")


class RetryableFailingFakeTranscriber:
    """Simulate a provider timeout so Temporal owns bounded retries."""

    async def transcribe(self, audio: bytes) -> TranscriptResult:
        assert audio
        raise TimeoutError("transcription timeout")


def _configure_local_activity(
    context,
    tmp_path,
    *,
    consent: VoiceConsent,
    provider: str = "local",
    model: str = "local-deterministic-v1",
) -> None:
    """Build one real deterministic-local activity with isolated durable stores."""
    request = RenderRequest(
        episode_id="temporal-activity-episode",
        episode_version="v1",
        segment_id="segment-1",
        speaker_id="host",
        expected_spoken_text="Temporal local activity renders durable audio.",
        voice_asset_id="voice-host-v1",
        provider=provider,
        model=model,
    )
    segment = SegmentWorkflowInput(
        segment_id=request.segment_id,
        render_request=request,
        consent=consent,
        critical_tokens=("Temporal", "local"),
    )
    episode = EpisodeWorkflowInput(
        episode_id=request.episode_id,
        episode_version=request.episode_version,
        segments=(segment,),
        max_attempts=1,
    )
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    records = FilesystemRenderRecordStore(tmp_path / "records", artifacts)
    renderer = DeterministicLocalRenderer()
    service = DurableRenderService(artifacts, records)
    context.values.update(
        activity=build_durable_render_activity(service, renderer, artifacts),
        artifacts=artifacts,
        artifacts_root=tmp_path / "artifacts",
        episode=episode,
        records=records,
        records_root=tmp_path / "records",
        renderer=renderer,
        request=request,
        segment=segment,
    )


def _payload(context) -> dict[str, object]:
    """Return the authenticated first-take payload for the scenario snapshot."""
    episode = context.values["episode"]
    segment = context.values["segment"]
    assert isinstance(episode, EpisodeWorkflowInput)
    assert isinstance(segment, SegmentWorkflowInput)
    return {
        "episode": episode.to_dict(),
        "segment": segment.to_dict(),
        "attempt": 1,
        "take": 0,
        "activity_key": activity_key_for(
            episode, "render", segment.segment_id, attempt=1, take=0
        ),
    }


async def _run_activity(context) -> dict[str, object]:
    """Run the public activity handler against one authenticated payload."""
    activity = context.values["activity"]
    assert callable(activity)
    result = await activity(_payload(context))
    assert isinstance(result, dict)
    return result


@given("a consented deterministic local render segment")
def consented_local_segment(context, tmp_path):
    """Provide matching local consent and no live provider configuration."""
    _configure_local_activity(
        context,
        tmp_path,
        consent=VoiceConsent("voice-host-v1", "consent-local-1", frozenset({"local"})),
    )


@given("a deterministic local render segment without matching provider consent")
def unconsented_local_segment(context, tmp_path):
    """Provide consent that deliberately excludes the local renderer."""
    _configure_local_activity(
        context,
        tmp_path,
        consent=VoiceConsent("voice-host-v1", "consent-other-1", frozenset({"other"})),
    )


@given("a consented hosted render segment without a quality evaluator")
def consented_hosted_segment_without_evaluator(context, tmp_path):
    """Provide a hosted request that must not fall back to local QA evidence."""
    _configure_local_activity(
        context,
        tmp_path,
        consent=VoiceConsent(
            "voice-host-v1", "consent-hosted-1", frozenset({"hosted"})
        ),
        provider="hosted",
        model="hosted-renderer-v1",
    )


@given("the first activity invocation fails after durable persistence")
def fail_after_persistence(context):
    """Simulate worker loss after the real handler has committed its evidence."""
    context.values["fail_after_persistence"] = True


@when("the provider-bound Temporal activity runs")
def run_provider_bound_activity(context):
    """Run the real deterministic activity without contacting a live provider."""
    try:
        context.values["result"] = asyncio.run(_run_activity(context))
    except ApplicationError as error:
        context.values["error"] = error


@when("the Temporal workflow retries the same activity key")
def retry_same_activity_key(context):
    """Replay a post-persistence activity failure with its unchanged payload."""

    async def run_with_one_post_persist_failure() -> dict[str, object]:
        result = await _run_activity(context)
        if context.values.pop("fail_after_persistence", False):
            raise RuntimeError("simulated worker loss after durable persistence")
        return result

    with pytest.raises(RuntimeError, match="worker loss"):
        asyncio.run(run_with_one_post_persist_failure())
    context.values["result"] = asyncio.run(run_with_one_post_persist_failure())


@then("the renderer is called once and the returned candidate passes local QA")
def candidate_passes_local_qa(context):
    """Require one provider dispatch and a hard-gated local quality result."""
    request = context.values["request"]
    renderer = context.values["renderer"]
    result = context.values["result"]
    assert isinstance(request, RenderRequest)
    assert isinstance(renderer, DeterministicLocalRenderer)
    assert isinstance(result, dict)
    assert renderer.calls == [request.idempotency_key]
    assert CandidateQuality.from_dict(result).passes_hard_gates is True


@then("exactly one immutable artifact and one cost event are persisted")
def one_artifact_and_cost_event(context):
    """Observe real filesystem persistence, rather than test-only counters."""
    artifacts_root = context.values["artifacts_root"]
    records = context.values["records"]
    records_root = context.values["records_root"]
    request = context.values["request"]
    assert isinstance(artifacts_root, type(records_root))
    assert isinstance(records_root, type(artifacts_root))
    assert isinstance(records, FilesystemRenderRecordStore)
    assert isinstance(request, RenderRequest)
    outcome = records.find(request.idempotency_key)
    assert outcome is not None
    assert outcome.cost_event is not None
    assert len(list(artifacts_root.rglob("*.wav"))) == 1
    assert len(list(records_root.rglob("*.json"))) == 1


@then("the workflow completes")
def workflow_completes(context):
    """Require the replay invocation to return a valid quality result."""
    result = context.values["result"]
    assert isinstance(result, dict)
    assert CandidateQuality.from_dict(result).passes_hard_gates is True


@then("the renderer is called once for that candidate")
def renderer_called_once_for_candidate(context):
    """Require durable replay to avoid a second local-provider dispatch."""
    request = context.values["request"]
    renderer = context.values["renderer"]
    assert isinstance(request, RenderRequest)
    assert isinstance(renderer, DeterministicLocalRenderer)
    assert renderer.calls == [request.idempotency_key]


@then("the cost event count remains one")
def cost_event_count_remains_one(context):
    """Require replay to retain one durable cost record for the candidate."""
    records = context.values["records"]
    request = context.values["request"]
    assert isinstance(records, FilesystemRenderRecordStore)
    assert isinstance(request, RenderRequest)
    outcome = records.find(request.idempotency_key)
    assert outcome is not None
    assert outcome.cost_event is not None


@then("the activity fails with a non-retryable rights error")
def activity_fails_with_rights_error(context):
    """Require invalid consent to map to Temporal's non-retryable rights error."""
    error = context.values["error"]
    assert isinstance(error, ApplicationError)
    assert error.type == "RightsFailureError"
    assert error.non_retryable is True


@then("the activity fails with a non-retryable workflow contract error")
def activity_fails_with_workflow_contract_error(context):
    """Require missing hosted QA evidence to fail before provider dispatch."""
    error = context.values["error"]
    assert isinstance(error, ApplicationError)
    assert error.type == "WorkflowContractError"
    assert error.non_retryable is True


@then("the renderer is never called")
def renderer_is_never_called(context):
    """Require consent rejection before deterministic renderer dispatch."""
    renderer = context.values["renderer"]
    assert isinstance(renderer, DeterministicLocalRenderer)
    assert renderer.calls == []


@when("the Temporal episode workflow runs with invalid consent")
def run_invalid_consent_episode(context):
    """Run the real workflow boundary with a two-attempt repair budget."""
    episode = replace(context.values["episode"], max_attempts=2)
    activity = context.values["activity"]

    async def run_workflow():
        async with (
            await WorkflowEnvironment.start_local() as environment,
            Worker(
                environment.client,
                task_queue="poddown-bdd-provider-temporal-render",
                workflows=[EpisodeRenderWorkflow],
                activities=[activity],
            ),
        ):
            return await TemporalEpisodeWorkflowService(
                environment.client, "poddown-bdd-provider-temporal-render"
            ).run_episode(episode)

    context.values["workflow_result"] = asyncio.run(run_workflow())


@then("the workflow fails on its first attempt with a rights error")
def workflow_fails_on_first_attempt_with_rights_error(context):
    """Require non-retryable rights failures to stop workflow repair."""
    result = context.values["workflow_result"]
    assert result.status == "failed"
    assert len(result.decisions) == 1
    assert result.decisions[0].attempt == 1
    assert result.decisions[0].failure_code == "RightsFailureError"
    assert result.terminal_failure is not None
    assert result.terminal_failure.failed_gates == ("rights",)


@then("no provider dispatch or durable cost record exists")
def no_provider_dispatch_or_durable_cost_record(context):
    """Require rights rejection to precede every provider and store boundary."""
    renderer = context.values["renderer"]
    records = context.values["records"]
    request = context.values["request"]
    assert isinstance(renderer, DeterministicLocalRenderer)
    assert isinstance(records, FilesystemRenderRecordStore)
    assert isinstance(request, RenderRequest)
    assert renderer.calls == []
    assert records.find(request.idempotency_key) is None


@when("the Temporal episode workflow runs with an unknown terminal activity failure")
def run_unknown_terminal_activity_episode(context):
    """Run the workflow with an unrecognized non-retryable activity error."""
    episode = replace(context.values["episode"], max_attempts=2)

    @activity.defn(name=RENDER_SEGMENT_ACTIVITY_NAME)
    async def fail_unknown(payload: dict[str, Any]) -> dict[str, Any]:
        del payload
        raise ApplicationError(
            "simulated unknown terminal activity failure",
            type="UnknownTerminalFailure",
            non_retryable=True,
        )

    async def run_workflow():
        async with (
            await WorkflowEnvironment.start_local() as environment,
            Worker(
                environment.client,
                task_queue="poddown-bdd-unknown-terminal-failure",
                workflows=[EpisodeRenderWorkflow],
                activities=[fail_unknown],
            ),
        ):
            return await TemporalEpisodeWorkflowService(
                environment.client, "poddown-bdd-unknown-terminal-failure"
            ).run_episode(episode)

    context.values["workflow_result"] = asyncio.run(run_workflow())


@then("the workflow fails on its first attempt with an activity gate")
def workflow_fails_on_first_attempt_with_activity_gate(context):
    """Require unknown terminal errors to retain an honest activity gate."""
    result = context.values["workflow_result"]
    assert result.status == "failed"
    assert len(result.decisions) == 1
    assert result.decisions[0].attempt == 1
    assert result.decisions[0].failure_code == "UnknownTerminalFailure"
    assert result.terminal_failure is not None
    assert result.terminal_failure.failed_gates == ("activity",)


@given("the third take is configured to exhaust transient retries")
def third_take_exhausts_transient_retries(context):
    """Configure one take to fail after Temporal exhausts its activity retries."""
    context.values["transient_take_index"] = 2


@when("the Temporal episode workflow runs with one repair attempt")
def run_episode_with_one_repair_attempt(context):
    """Run the real workflow with one bounded segment-repair opportunity."""
    episode = replace(context.values["episode"], max_attempts=2)
    real_activity = context.values["activity"]
    transient_take_index = context.values["transient_take_index"]

    @activity.defn(name=RENDER_SEGMENT_ACTIVITY_NAME)
    async def fail_one_take(payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("take") == transient_take_index:
            raise ApplicationError(
                "simulated transient provider failure",
                type="TransientProviderError",
            )
        result = await real_activity(payload)
        assert isinstance(result, dict)
        return result

    async def run_workflow():
        async with (
            await WorkflowEnvironment.start_local() as environment,
            Worker(
                environment.client,
                task_queue="poddown-bdd-partial-take-retry",
                workflows=[EpisodeRenderWorkflow],
                activities=[fail_one_take],
            ),
        ):
            return await TemporalEpisodeWorkflowService(
                environment.client, "poddown-bdd-partial-take-retry"
            ).run_episode(episode)

    context.values["partial_take_result"] = asyncio.run(run_workflow())


@then("a passing take is accepted without segment repair")
def passing_take_is_accepted_without_repair(context):
    """Require successful takes to be selected despite one exhausted sibling."""
    result = context.values["partial_take_result"]
    assert result.status == "completed"
    assert len(result.decisions) == 1
    assert result.decisions[0].attempt == 1
    assert result.decisions[0].accepted_candidate_id is not None


@then("the transient take is excluded from provider usage")
def transient_take_is_excluded_from_provider_usage(context):
    """Require the failed take to produce no local renderer or durable record."""
    request = context.values["request"]
    renderer = context.values["renderer"]
    records = context.values["records"]
    transient_take_index = context.values["transient_take_index"]
    assert isinstance(request, RenderRequest)
    assert isinstance(renderer, DeterministicLocalRenderer)
    assert isinstance(records, FilesystemRenderRecordStore)
    transient_key = replace(request, take_index=transient_take_index).idempotency_key
    assert transient_key not in renderer.calls
    assert records.find(transient_key) is None


def _configure_injected_transcriber(context, tmp_path, transcriber: object) -> None:
    """Keep injected-transcriber scenarios on the same isolated local fixture."""
    _configure_local_activity(
        context,
        tmp_path,
        consent=VoiceConsent("voice-host-v1", "consent-local-1", frozenset({"local"})),
    )
    context.values["transcriber"] = transcriber
    context.values["quality_records"] = FilesystemQualityRecordStore(
        tmp_path / "quality"
    )
    context.values["transcription_records"] = FilesystemTranscriptionRecordStore(
        tmp_path / "transcriptions"
    )


def _run_injected_activity(context) -> dict[str, object]:
    """Build the planned injected activity boundary and execute one payload."""
    artifacts = context.values["artifacts"]
    records = context.values["records"]
    renderer = context.values["renderer"]
    transcriber = context.values["transcriber"]
    quality_records = context.values["quality_records"]
    transcription_records = context.values["transcription_records"]
    assert isinstance(artifacts, FilesystemArtifactStore)
    assert isinstance(records, FilesystemRenderRecordStore)
    assert isinstance(renderer, DeterministicLocalRenderer)
    assert isinstance(quality_records, FilesystemQualityRecordStore)
    assert isinstance(transcription_records, FilesystemTranscriptionRecordStore)
    activity_handler = build_durable_render_activity(
        DurableRenderService(artifacts, records),
        renderer,
        artifacts,
        transcriber=transcriber,
        quality_records=quality_records,
        transcription_records=transcription_records,
    )
    return asyncio.run(activity_handler(_payload(context)))


@given("a consented segment with an injected matching transcriber")
def matching_injected_transcriber(context, tmp_path):
    _configure_injected_transcriber(
        context,
        tmp_path,
        MatchingFakeTranscriber("Temporal local activity renders durable audio."),
    )


@given("a consented segment with an injected transcriber missing a critical token")
def missing_token_injected_transcriber(context, tmp_path):
    _configure_injected_transcriber(
        context,
        tmp_path,
        MatchingFakeTranscriber("local activity renders durable audio."),
    )


@given("a consented segment with a malformed non-retryable transcriber")
def malformed_non_retryable_transcriber(context, tmp_path):
    _configure_injected_transcriber(
        context, tmp_path, MalformedNonRetryableFakeTranscriber()
    )


@given("a consented segment with a retryable failing transcriber")
def retryable_failing_transcriber(context, tmp_path):
    _configure_injected_transcriber(
        context, tmp_path, RetryableFailingFakeTranscriber()
    )


@when("the provider-bound activity runs with the injected transcriber")
def run_injected_transcriber_activity(context):
    try:
        context.values["result"] = _run_injected_activity(context)
    except ApplicationError as error:
        context.values["error"] = error


@then("the returned quality includes transcription provenance")
def quality_includes_transcription_provenance(context):
    result = context.values["result"]
    assert isinstance(result, dict)
    transcription = result["transcription"]
    assert transcription["provider"] == "fake-transcriber"
    assert transcription["model"] == "fake-model-v1"
    assert transcription["request_id"] == "fake-request-1"
    assert transcription["cost"] == "0.01"


@then("the returned quality requires segment rerender")
def quality_requires_segment_rerender(context):
    result = context.values["result"]
    assert isinstance(result, dict)
    fidelity = result["fidelity"]
    assert fidelity["passed"] is False
    assert fidelity["accuracy"] < 1.0
    assert fidelity["rerender_scope"] == "segment"


@then("transcription fails closed without canonical-text fallback")
def transcription_fails_closed_without_fallback(context):
    error = context.values["error"]
    assert isinstance(error, ApplicationError)
    assert error.type == "TranscriptionFailureError"
    assert error.non_retryable is True
    assert "result" not in context.values
    assert context.values["renderer"].calls


@when("the Temporal episode workflow runs with retryable transcription failures")
def run_retryable_transcription_workflow(context):
    episode = replace(context.values["episode"], max_attempts=1)
    artifacts = context.values["artifacts"]
    records = context.values["records"]
    renderer = context.values["renderer"]
    transcriber = context.values["transcriber"]
    quality_records = context.values["quality_records"]
    transcription_records = context.values["transcription_records"]
    assert isinstance(artifacts, FilesystemArtifactStore)
    assert isinstance(records, FilesystemRenderRecordStore)
    assert isinstance(renderer, DeterministicLocalRenderer)
    assert isinstance(quality_records, FilesystemQualityRecordStore)
    assert isinstance(transcription_records, FilesystemTranscriptionRecordStore)

    activity_handler = build_durable_render_activity(
        DurableRenderService(artifacts, records),
        renderer,
        artifacts,
        transcriber=transcriber,
        quality_records=quality_records,
        transcription_records=transcription_records,
    )

    async def run_workflow():
        async with (
            await WorkflowEnvironment.start_local() as environment,
            Worker(
                environment.client,
                task_queue="poddown-bdd-transcription-retry",
                workflows=[EpisodeRenderWorkflow],
                activities=[activity_handler],
            ),
        ):
            return await TemporalEpisodeWorkflowService(
                environment.client, "poddown-bdd-transcription-retry"
            ).run_episode(episode)

    context.values["workflow_result"] = asyncio.run(run_workflow())


@then("the workflow fails with a transcription gate after bounded retries")
def transcription_retry_exhaustion_is_structured(context):
    result = context.values["workflow_result"]
    assert result.status == "failed"
    assert result.terminal_failure is not None
    assert result.terminal_failure.failed_gates == ("transcription",)
    assert result.terminal_failure.last_error_code == "TranscriptionTransientError"


@when("the provider-bound activity runs twice with the same durable quality key")
def replay_injected_transcriber_activity(context):
    first = _run_injected_activity(context)
    request = context.values["request"]
    records_root = context.values["records_root"]
    assert isinstance(request, RenderRequest)
    assert isinstance(records_root, type(context.values["artifacts_root"]))
    (records_root / "records" / f"{request.idempotency_key}.json").unlink()
    second = _run_injected_activity(context)
    context.values["results"] = (first, second)


@then("transcription dispatch count remains one")
def transcription_dispatch_count_remains_one(context):
    transcriber = context.values["transcriber"]
    assert isinstance(transcriber, MatchingFakeTranscriber)
    assert transcriber.calls == 1
    first, second = context.values["results"]
    assert first["transcription"] == second["transcription"]
    renderer = context.values["renderer"]
    request = context.values["request"]
    assert isinstance(renderer, DeterministicLocalRenderer)
    assert isinstance(request, RenderRequest)
    assert renderer.calls == [request.idempotency_key]


@then("the quality evidence is explicitly deterministic local and zero-cost")
def deterministic_local_quality_is_explicit_and_zero_cost(context):
    result = context.values["result"]
    assert isinstance(result, dict)
    transcription = result["transcription"]
    assert transcription["provider"] == "local"
    assert transcription["mode"] == "deterministic-local"
    assert transcription["cost"] == "0"
