"""Strict YAML profile loading and allowlisted document metadata resolution."""

from collections.abc import Collection, Mapping
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from poddown.content.models import (
    Profile,
    SpeakerProfile,
    VoiceAsset,
    VoiceConsent,
    freeze_mapping,
)


class _SpeakerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    speaker_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    voice_asset_id: str = Field(min_length=1)


class _ProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    format_type: Literal["narration", "dialogue"]
    target_minutes: int = Field(ge=1, le=180)
    speakers: list[_SpeakerModel] = Field(min_length=1)
    style: dict[str, object] = Field(default_factory=dict)
    audio: dict[str, object] = Field(default_factory=dict)
    quality: dict[str, object] = Field(default_factory=dict)
    document_overridable: set[str] = Field(default_factory=set)


def _profile_from_model(model: _ProfileModel) -> Profile:
    speakers = tuple(
        SpeakerProfile(item.speaker_id, item.display_name, item.voice_asset_id)
        for item in model.speakers
    )
    if len({speaker.speaker_id for speaker in speakers}) != len(speakers):
        raise ValueError("Profile speaker IDs must be unique")
    if model.format_type == "dialogue" and len(speakers) < 2:
        raise ValueError("Dialogue profiles require at least two speakers")
    return Profile(
        profile_id=model.profile_id,
        version=model.version,
        format_type=model.format_type,
        target_minutes=model.target_minutes,
        speakers=speakers,
        style=freeze_mapping(model.style),
        audio=freeze_mapping(model.audio),
        quality=freeze_mapping(model.quality),
        document_overridable=frozenset(model.document_overridable),
    )


def load_profile(
    yaml_text: str,
    active_voice_assets: Collection[VoiceAsset],
    valid_consents: Collection[VoiceConsent],
) -> Profile:
    """Load a strict profile only when every referenced voice is approved locally."""
    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid profile YAML: {error}") from error
    if not isinstance(parsed, dict):
        raise ValueError("Profile YAML must be an object")
    try:
        model = _ProfileModel.model_validate(parsed)
    except ValidationError as error:
        raise ValueError(f"Invalid profile: {error}") from error
    profile = _profile_from_model(model)
    assets = {asset.asset_id: asset for asset in active_voice_assets}
    consents = {consent.asset_id: consent for consent in valid_consents}
    for speaker in profile.speakers:
        asset = assets.get(speaker.voice_asset_id)
        consent = consents.get(speaker.voice_asset_id)
        if asset is None or not asset.active:
            raise ValueError(f"Voice asset is not active: {speaker.voice_asset_id}")
        if consent is None or not consent.valid:
            raise ValueError(f"Voice consent is not valid: {speaker.voice_asset_id}")
    return profile


def resolve_profile_metadata(
    profile: Profile, frontmatter: Mapping[str, object]
) -> Profile:
    """Return a new profile after applying its explicit document override allowlist."""
    metadata = frontmatter.get("poddown", frontmatter)
    if not isinstance(metadata, Mapping):
        return profile
    values = {
        field: value
        for field, value in metadata.items()
        if field in profile.document_overridable
        and field in {"format_type", "target_minutes", "style", "audio", "quality"}
    }
    if not values:
        return profile
    try:
        model = _ProfileModel.model_validate(
            {
                "profile_id": profile.profile_id,
                "version": profile.version,
                "format_type": values.get("format_type", profile.format_type),
                "target_minutes": values.get("target_minutes", profile.target_minutes),
                "speakers": [
                    {
                        "speaker_id": speaker.speaker_id,
                        "display_name": speaker.display_name,
                        "voice_asset_id": speaker.voice_asset_id,
                    }
                    for speaker in profile.speakers
                ],
                "style": values.get("style", dict(profile.style)),
                "audio": values.get("audio", dict(profile.audio)),
                "quality": values.get("quality", dict(profile.quality)),
                "document_overridable": profile.document_overridable,
            }
        )
    except ValidationError as error:
        raise ValueError(f"Invalid profile override: {error}") from error
    return _profile_from_model(model)
