"""Tests for the durable-audio fail-closed rights policy."""

import pytest

from poddown.audio.contracts import RenderRequest
from poddown.audio.rights import RightsDeniedError, VoiceConsent, require_render_rights


def _request() -> RenderRequest:
    return RenderRequest(
        episode_id="episode-001",
        episode_version="v1",
        segment_id="segment-001",
        speaker_id="host",
        expected_spoken_text="The rate is 13.9 hertz.",
        voice_asset_id="voice-host-v1",
        provider="local",
        model="local-deterministic-v1",
    )


@pytest.mark.parametrize(
    ("consent", "reason"),
    [
        (None, "missing"),
        (
            VoiceConsent("voice-host-v1", "evidence-001", frozenset({"local"}), False),
            "invalid",
        ),
        (VoiceConsent("voice-host-v1", "", frozenset({"local"})), "evidence"),
        (VoiceConsent("another-voice", "evidence-001", frozenset({"local"})), "asset"),
        (
            VoiceConsent("voice-host-v1", "evidence-001", frozenset({"other"})),
            "provider",
        ),
    ],
)
def test_require_render_rights_denies_each_fail_closed_reason(
    consent: VoiceConsent | None, reason: str
):
    """Every missing or mismatched consent component must block dispatch."""
    with pytest.raises(RightsDeniedError, match=reason):
        require_render_rights(_request(), consent)


def test_require_render_rights_accepts_complete_matching_consent():
    """A valid evidenced consent explicitly allowing the provider proceeds."""
    consent = VoiceConsent("voice-host-v1", "evidence-001", frozenset({"local"}))

    assert require_render_rights(_request(), consent) is None
