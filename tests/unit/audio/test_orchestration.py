"""Contract tests for deterministic local orchestration fixtures."""

import pytest

from poddown.audio.contracts import RenderRequest
from poddown.audio.orchestration import (
    LocalEpisodeWorkflowService,
    LocalOrchestrationError,
)
from poddown.audio.rights import VoiceConsent
from poddown.audio.workflow import EpisodeWorkflowInput, SegmentWorkflowInput


def episode_input() -> EpisodeWorkflowInput:
    segment_id = "segment-1"
    return EpisodeWorkflowInput(
        episode_id="episode-1",
        episode_version="v1",
        segments=(
            SegmentWorkflowInput(
                segment_id=segment_id,
                render_request=RenderRequest(
                    episode_id="episode-1",
                    episode_version="v1",
                    segment_id=segment_id,
                    speaker_id="host",
                    expected_spoken_text="A deterministic segment.",
                    voice_asset_id="voice-host-v1",
                    provider="local",
                    model="local-v1",
                ),
                consent=VoiceConsent(
                    "voice-host-v1", "consent-1", frozenset({"local"})
                ),
                critical_tokens=("deterministic",),
            ),
        ),
    )


def fixture(attempts: list[object] | None = None) -> dict[str, object]:
    return {
        "mode": "deterministic-local-demo",
        "attempts": {"segment-1": attempts or [["take-0", "take-1", "take-2"]]},
        "dispatches": [],
        "accepted_cost_events": [],
    }


@pytest.mark.parametrize(
    "invalid_fixture",
    [
        {},
        {"mode": "live"},
        {"mode": "deterministic-local-demo", "attempts": []},
        {
            "mode": "deterministic-local-demo",
            "attempts": {},
            "dispatches": [],
        },
    ],
)
def test_local_fixture_validation_fails_closed(invalid_fixture: dict[str, object]):
    with pytest.raises(LocalOrchestrationError):
        LocalEpisodeWorkflowService(invalid_fixture)


def test_local_fixture_rejects_more_than_three_candidate_takes():
    service = LocalEpisodeWorkflowService(
        fixture([["take-0", "take-1", "take-2", "take-3"]])
    )

    with pytest.raises(LocalOrchestrationError, match="three takes"):
        service.run_episode(episode_input())


@pytest.mark.parametrize(
    "candidate",
    [
        {"fidelity": "invalid"},
        {"diagnostics": "invalid"},
        {"candidate_id": 1},
        {"candidate_id": ""},
        {"pronunciation_passed": 1},
        {"soft_score": "not-a-decimal"},
    ],
)
def test_local_candidate_fixture_rejects_malformed_quality(candidate):
    service = LocalEpisodeWorkflowService(fixture([[candidate]]))

    with pytest.raises(LocalOrchestrationError, match="candidate"):
        service.run_episode(episode_input())
