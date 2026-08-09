"""Executable acceptance tests for durable single-segment audio rendering."""

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal

import pytest
from poddown.audio.contracts import RenderRequest
from poddown.audio.local import DeterministicLocalRenderer
from poddown.audio.render import DurableRenderService, RenderRejectedError
from poddown.audio.rights import VoiceConsent
from poddown.audio.storage import FilesystemArtifactStore, FilesystemRenderRecordStore
from pytest_bdd import given, scenarios, then, when

scenarios("../features/durable_audio.feature")

EXPECTED_SPOKEN_TEXT = "The rate is 13.9 hertz, not 14 hertz."


@pytest.fixture
def audio_context(tmp_path):
    """Build one isolated durable render service and filesystem store per scenario."""
    artifacts_root = tmp_path / "artifacts"
    records_root = tmp_path / "records"
    request = RenderRequest(
        episode_id="demo-episode",
        episode_version="v1",
        segment_id="segment-001",
        speaker_id="host",
        expected_spoken_text=EXPECTED_SPOKEN_TEXT,
        voice_asset_id="voice-host-v1",
        provider="local",
        model="local-deterministic-v1",
    )
    renderer = DeterministicLocalRenderer()
    return {
        "artifacts_root": artifacts_root,
        "consent": VoiceConsent(
            "voice-host-v1", "consent-demo-001", frozenset({"local"})
        ),
        "records_root": records_root,
        "renderer": renderer,
        "request": request,
        "service": DurableRenderService(
            FilesystemArtifactStore(artifacts_root),
            FilesystemRenderRecordStore(records_root),
        ),
    }


def _render(audio_context, *, take_count=1):
    return audio_context["service"].render_takes(
        audio_context["request"],
        audio_context["consent"],
        audio_context["renderer"],
        take_count=take_count,
    )


@given("a rights-cleared render request")
def rights_cleared_render_request(audio_context):
    assert audio_context["consent"].valid is True


@given("a render request without voice consent")
def render_request_without_voice_consent(audio_context):
    audio_context["consent"] = None


@given("the renderer cannot pin the requested voice")
def renderer_cannot_pin_requested_voice(audio_context):
    renderer = audio_context["renderer"]
    renderer.capabilities = replace(renderer.capabilities, voice_pinning=False)


@when("the request is rendered with three local takes")
def render_three_local_takes(audio_context):
    audio_context["outcomes"] = _render(audio_context, take_count=3)


@when("the request is rendered with one local take")
def render_one_local_take(audio_context):
    with pytest.raises(RenderRejectedError):
        _render(audio_context)


@when("the same request is rendered twice with one local take")
def render_same_request_twice(audio_context):
    audio_context["first_outcomes"] = _render(audio_context)
    audio_context["second_outcomes"] = _render(audio_context)


@when("the request is rendered with two local takes")
def render_two_local_takes(audio_context):
    audio_context["outcomes"] = _render(audio_context, take_count=2)


@then("three immutable candidates and artifacts are returned")
def three_immutable_candidates_and_artifacts(audio_context):
    outcomes = audio_context["outcomes"]
    assert len(outcomes) == 3
    assert all(outcome.candidate.artifact.sha256 for outcome in outcomes)
    with pytest.raises(FrozenInstanceError):
        outcomes[0].candidate.segment_id = "changed"


@then("every candidate preserves the expected-spoken text")
def candidates_preserve_expected_spoken_text(audio_context):
    assert all(
        outcome.candidate.expected_spoken_text == EXPECTED_SPOKEN_TEXT
        for outcome in audio_context["outcomes"]
    )


@then("every candidate records zero-cost local usage")
def candidates_record_zero_cost_local_usage(audio_context):
    for outcome in audio_context["outcomes"]:
        assert outcome.candidate.provider == "local"
        assert outcome.candidate.cost == Decimal("0")
        assert outcome.cost_event is not None
        assert outcome.cost_event.cost == Decimal("0")


@then("rendering is rejected before the renderer is called")
def rendering_rejected_before_renderer_call(audio_context):
    assert audio_context["renderer"].calls == []


@then("no artifact or cost event is recorded")
def no_artifact_or_cost_event_recorded(audio_context):
    assert not audio_context["artifacts_root"].exists()
    assert not audio_context["records_root"].exists()


@then("the second result is marked as replayed")
def second_result_marked_replayed(audio_context):
    (outcome,) = audio_context["second_outcomes"]
    assert outcome.replayed is True
    assert outcome.cost_event is None


@then("the renderer is called only once")
def renderer_called_only_once(audio_context):
    assert len(audio_context["renderer"].calls) == 1


@then("exactly one cost event exists")
def exactly_one_cost_event_exists(audio_context):
    outcomes = (*audio_context["first_outcomes"], *audio_context["second_outcomes"])
    assert sum(outcome.cost_event is not None for outcome in outcomes) == 1


@then("the two candidates have different candidate identities")
def candidates_have_distinct_identities(audio_context):
    first, second = audio_context["outcomes"]
    assert first.candidate.candidate_id != second.candidate.candidate_id


@then("the two artifacts have different content digests")
def artifacts_have_distinct_content_digests(audio_context):
    first, second = audio_context["outcomes"]
    assert first.candidate.artifact.sha256 != second.candidate.artifact.sha256
