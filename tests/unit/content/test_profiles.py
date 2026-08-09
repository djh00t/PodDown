"""Tests for strict, immutable content profiles."""

from dataclasses import FrozenInstanceError

import pytest

from poddown.content.models import VoiceAsset, VoiceConsent
from poddown.content.profiles import load_profile, resolve_profile_metadata

VALID_PROFILE = """profile_id: technical-dialogue
version: 1.0.0
format_type: dialogue
target_minutes: 12
speakers:
  - speaker_id: host
    display_name: Host
    voice_asset_id: voice-host
  - speaker_id: analyst
    display_name: Analyst
    voice_asset_id: voice-analyst
style:
  tone: precise
audio:
  pace: measured
quality:
  fidelity: strict
document_overridable:
  - target_minutes
"""

SPEAKERS = """speakers:
  - speaker_id: host
    display_name: Host
    voice_asset_id: voice-host
  - speaker_id: analyst
    display_name: Analyst
    voice_asset_id: voice-analyst
"""

SECOND_SPEAKER = """  - speaker_id: analyst
    display_name: Analyst
    voice_asset_id: voice-analyst
"""


def _assets(*, active: bool = True):
    return (
        (
            VoiceAsset("voice-host", active),
            VoiceAsset("voice-analyst", active),
        )
        if active
        else (VoiceAsset("voice-host", False), VoiceAsset("voice-analyst", True))
    )


def _consents(*, valid: bool = True):
    return (
        VoiceConsent("voice-host", valid),
        VoiceConsent("voice-analyst", True),
    )


def test_load_profile_returns_an_immutable_validated_profile():
    """A valid profile must retain its speaker-to-asset contract immutably."""
    profile = load_profile(VALID_PROFILE, _assets(), _consents())

    assert profile.profile_id == "technical-dialogue"
    assert tuple(speaker.speaker_id for speaker in profile.speakers) == (
        "host",
        "analyst",
    )
    assert profile.style == {"tone": "precise"}
    with pytest.raises(FrozenInstanceError):
        profile.target_minutes = 14
    with pytest.raises(TypeError):
        profile.style["tone"] = "casual"


@pytest.mark.parametrize(
    ("yaml_text", "assets", "consents"),
    [
        (
            VALID_PROFILE.replace("version: 1.0.0", "version: 1.0.0\nextra: no"),
            _assets(),
            _consents(),
        ),
        (
            VALID_PROFILE.replace("target_minutes: 12", "target_minutes: 0"),
            _assets(),
            _consents(),
        ),
        (
            VALID_PROFILE.replace(SPEAKERS, "speakers: []\n"),
            _assets(),
            _consents(),
        ),
        (VALID_PROFILE, _assets(active=False), _consents()),
        (VALID_PROFILE, _assets(), _consents(valid=False)),
        (
            VALID_PROFILE.replace(SECOND_SPEAKER, ""),
            _assets(),
            _consents(),
        ),
    ],
)
def test_load_profile_rejects_invalid_or_unconsented_contracts(
    yaml_text, assets, consents
):
    """Unknown metadata or unusable voices must fail before script generation."""
    with pytest.raises(ValueError):
        load_profile(yaml_text, assets, consents)


def test_resolve_profile_metadata_applies_only_allowlisted_overrides():
    """Document metadata cannot silently alter profile-owned audio or quality policy."""
    profile = load_profile(VALID_PROFILE, _assets(), _consents())

    resolved = resolve_profile_metadata(
        profile,
        {
            "poddown": {
                "target_minutes": 15,
                "format_type": "narration",
                "audio": {"pace": "fast"},
            }
        },
    )

    assert resolved is not profile
    assert resolved.target_minutes == 15
    assert resolved.format_type == "dialogue"
    assert resolved.audio == {"pace": "measured"}
