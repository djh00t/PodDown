"""Tests for strict, immutable content profiles."""

from dataclasses import FrozenInstanceError

import pytest

from poddown.content.models import (
    Profile,
    ScriptTurn,
    ScriptVersion,
    SpeakerProfile,
    VoiceAsset,
    VoiceConsent,
)
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


class _MutableScalar(str):
    def __new__(cls, value: str):
        instance = str.__new__(cls, value)
        instance.state = ["original"]
        return instance

    def mutate(self) -> None:
        self.state.append("changed")


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


def test_nested_profile_values_are_copied_and_frozen():
    """A frozen profile must not retain caller-owned nested mutable values."""
    style = {"nested": {"values": ["original"]}}
    profile = Profile(
        "profile-1",
        "1.0.0",
        "narration",
        12,
        (SpeakerProfile("speaker-1", "Speaker", "voice-1"),),
        style,
        {},
        {},
        frozenset(),
    )

    style["nested"]["values"].append("changed")

    assert profile.style["nested"]["values"] == ("original",)
    with pytest.raises(TypeError):
        profile.style["nested"]["values"] += ("changed",)


def test_profile_rejects_unsupported_mutable_metadata_values():
    """Unrecognized mutable values must not remain shared through a frozen profile."""
    with pytest.raises(TypeError, match="metadata"):
        Profile(
            "profile-1",
            "1.0.0",
            "narration",
            12,
            (SpeakerProfile("speaker-1", "Speaker", "voice-1"),),
            {"raw": bytearray(b"mutable")},
            {},
            {},
            frozenset(),
        )


def test_mutable_allowed_scalar_subclass_is_converted_in_profile_metadata():
    """A frozen profile must not retain mutable state on a scalar subclass."""
    scalar = _MutableScalar("precise")
    profile = Profile(
        "profile-1",
        "1.0.0",
        "narration",
        12,
        (SpeakerProfile("speaker-1", "Speaker", "voice-1"),),
        {"tone": scalar},
        {},
        {},
        frozenset(),
    )

    scalar.mutate()

    assert type(profile.style["tone"]) is str
    assert profile.style["tone"] == "precise"


@pytest.mark.parametrize(
    "builder",
    [
        lambda: Profile(
            "profile-1",
            "1.0.0",
            "invalid",
            12,
            (SpeakerProfile("speaker-1", "Speaker", "voice-1"),),
            {},
            {},
            {},
            frozenset(),
        ),
        lambda: Profile(
            "profile-1",
            "1.0.0",
            "narration",
            0,
            (SpeakerProfile("speaker-1", "Speaker", "voice-1"),),
            {},
            {},
            {},
            frozenset(),
        ),
        lambda: ScriptTurn("turn-1", "speaker-1", "text", "unsupported", (), ()),
        lambda: ScriptVersion("script-1", "not-a-hash", "profile-1", (), "a" * 64),
    ],
)
def test_public_models_reject_invalid_values(builder):
    """Frozen dataclasses must enforce their literal and hash contracts at runtime."""
    with pytest.raises(ValueError):
        builder()


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


def test_load_profile_fails_closed_on_duplicate_rights_records():
    """Conflicting or repeated rights identities must not use last-record-wins."""
    with pytest.raises(ValueError, match="duplicate"):
        load_profile(
            VALID_PROFILE,
            (*_assets(), VoiceAsset("voice-host", True)),
            _consents(),
        )

    with pytest.raises(ValueError, match="duplicate"):
        load_profile(
            VALID_PROFILE,
            _assets(),
            (*_consents(valid=False), VoiceConsent("voice-host", True)),
        )


@pytest.mark.parametrize(
    "target_value",
    ['"12"', "12.0", "true"],
)
def test_load_profile_rejects_coercible_but_non_integer_target_minutes(target_value):
    """YAML strings, floats, and booleans must not become valid minute counts."""
    with pytest.raises(ValueError):
        load_profile(
            VALID_PROFILE.replace(
                "target_minutes: 12", f"target_minutes: {target_value}"
            ),
            _assets(),
            _consents(),
        )


def test_load_profile_rejects_duplicate_yaml_keys():
    """A duplicate profile key is ambiguous and must fail before validation."""
    duplicate_yaml = VALID_PROFILE.replace(
        "version: 1.0.0", "version: 1.0.0\nversion: 2.0.0"
    )

    with pytest.raises(ValueError, match="duplicate"):
        load_profile(duplicate_yaml, _assets(), _consents())


def test_resolve_profile_metadata_applies_only_allowlisted_overrides():
    """Document metadata cannot silently alter profile-owned audio or quality policy."""
    profile = load_profile(VALID_PROFILE, _assets(), _consents())

    resolved = resolve_profile_metadata(
        profile,
        {
            "poddown": {
                "target_minutes": 15,
                "format": "narration",
                "audio": {"pace": "fast"},
            }
        },
    )

    assert resolved is not profile
    assert resolved.target_minutes == 15
    assert resolved.format_type == "dialogue"
    assert resolved.audio == {"pace": "measured"}


def test_resolve_profile_metadata_maps_document_format_and_rejects_unknown_keys():
    """Only the documented PodDown object may map external metadata fields."""
    profile = load_profile(
        VALID_PROFILE.replace("- target_minutes", "- format"),
        _assets(),
        _consents(),
    )

    resolved = resolve_profile_metadata(
        profile, {"title": "not metadata", "poddown": {"format": "narration"}}
    )
    assert resolved is not profile
    assert resolved.format_type == "narration"

    top_level_only = resolve_profile_metadata(
        profile, {"format": "narration", "poddown_title": "ignored"}
    )
    assert top_level_only is not profile
    assert top_level_only.format_type == "dialogue"

    with pytest.raises(ValueError, match="extra|unknown"):
        resolve_profile_metadata(profile, {"poddown": {"unsupported": True}})

    with pytest.raises(ValueError, match="object"):
        resolve_profile_metadata(profile, {"poddown": []})


def test_resolve_profile_metadata_rejects_undocumented_format_type_aliases():
    """Document metadata exposes only the external PodDown format key."""
    profile = load_profile(
        VALID_PROFILE.replace("- target_minutes", "- format"),
        _assets(),
        _consents(),
    )

    with pytest.raises(ValueError, match="extra|format_type"):
        resolve_profile_metadata(profile, {"poddown": {"format_type": "narration"}})
    with pytest.raises(ValueError, match="extra|format_type"):
        resolve_profile_metadata(
            profile,
            {"poddown": {"format": "narration", "format_type": "dialogue"}},
        )
