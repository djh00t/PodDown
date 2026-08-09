"""Strict YAML profile loading and allowlisted document metadata resolution."""

from collections.abc import Collection, Mapping
from dataclasses import replace
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from poddown.content.models import (
    DOCUMENT_OVERRIDABLE_FIELDS,
    Profile,
    SpeakerProfile,
    VoiceAsset,
    VoiceConsent,
    freeze_mapping,
)


class _StrictLoader(yaml.SafeLoader):
    """YAML loader that rejects duplicate mapping keys at every nesting level."""


def _construct_mapping(
    loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "mapping keys must be hashable",
                key_node.start_mark,
            ) from error
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"duplicate key: {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def _load_yaml(yaml_text: str) -> object:
    try:
        return yaml.load(yaml_text, Loader=_StrictLoader)
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid YAML: {error}") from error


class _SpeakerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    speaker_id: StrictStr = Field(min_length=1)
    display_name: StrictStr = Field(min_length=1)
    voice_asset_id: StrictStr = Field(min_length=1)


class _ProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: StrictStr = Field(min_length=1)
    version: StrictStr = Field(min_length=1)
    format_type: Literal["narration", "dialogue"]
    target_minutes: StrictInt = Field(ge=1, le=180)
    speakers: list[_SpeakerModel] = Field(min_length=1)
    style: dict[StrictStr, object] = Field(default_factory=dict)
    audio: dict[StrictStr, object] = Field(default_factory=dict)
    quality: dict[StrictStr, object] = Field(default_factory=dict)
    document_overridable: list[StrictStr] = Field(default_factory=list)


class _DocumentMetadataModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile: StrictStr | None = None
    format: Literal["narration", "dialogue"] | None = None
    format_type: Literal["narration", "dialogue"] | None = None
    target_minutes: StrictInt | None = Field(default=None, ge=1, le=180)
    pronunciation_overrides: dict[StrictStr, StrictStr] | None = None
    style: dict[StrictStr, object] | None = None
    audio: dict[StrictStr, object] | None = None
    quality: dict[StrictStr, object] | None = None


def _profile_from_model(model: _ProfileModel) -> Profile:
    speakers = tuple(
        SpeakerProfile(item.speaker_id, item.display_name, item.voice_asset_id)
        for item in model.speakers
    )
    if len({speaker.speaker_id for speaker in speakers}) != len(speakers):
        raise ValueError("Profile speaker IDs must be unique")
    if len(set(model.document_overridable)) != len(model.document_overridable):
        raise ValueError("Profile document_overridable contains duplicates")
    unknown = set(model.document_overridable) - DOCUMENT_OVERRIDABLE_FIELDS
    if unknown:
        raise ValueError(f"Unknown document-overridable fields: {sorted(unknown)}")
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


def _unique_rights_records(
    records: Collection[VoiceAsset] | Collection[VoiceConsent], record_kind: str
) -> dict[str, VoiceAsset | VoiceConsent]:
    indexed: dict[str, VoiceAsset | VoiceConsent] = {}
    for record in records:
        asset_id = record.asset_id
        if asset_id in indexed:
            raise ValueError(f"duplicate {record_kind} record: {asset_id}")
        indexed[asset_id] = record
    return indexed


def load_profile(
    yaml_text: str,
    active_voice_assets: Collection[VoiceAsset],
    valid_consents: Collection[VoiceConsent],
) -> Profile:
    """Load a strict profile only when every referenced voice is approved locally."""
    parsed = _load_yaml(yaml_text)
    if not isinstance(parsed, dict):
        raise ValueError("Profile YAML must be an object")
    try:
        model = _ProfileModel.model_validate(parsed)
    except ValidationError as error:
        raise ValueError(f"Invalid profile: {error}") from error
    profile = _profile_from_model(model)
    assets = _unique_rights_records(active_voice_assets, "voice asset")
    consents = _unique_rights_records(valid_consents, "voice consent")
    for speaker in profile.speakers:
        asset = assets.get(speaker.voice_asset_id)
        consent = consents.get(speaker.voice_asset_id)
        if not isinstance(asset, VoiceAsset) or not asset.active:
            raise ValueError(f"Voice asset is not active: {speaker.voice_asset_id}")
        if not isinstance(consent, VoiceConsent) or not consent.valid:
            raise ValueError(f"Voice consent is not valid: {speaker.voice_asset_id}")
    return profile


def resolve_profile_metadata(
    profile: Profile, frontmatter: Mapping[str, object]
) -> Profile:
    """Resolve only validated, explicitly allowlisted PodDown metadata fields."""
    if "poddown" not in frontmatter:
        return replace(profile)
    metadata = frontmatter["poddown"]
    if not isinstance(metadata, Mapping):
        raise ValueError("PodDown frontmatter must be an object")
    try:
        parsed = _DocumentMetadataModel.model_validate(metadata)
    except ValidationError as error:
        raise ValueError(f"Invalid PodDown metadata: {error}") from error

    values: dict[str, object] = {}
    allowlist = profile.document_overridable
    if parsed.format is not None and (
        "format" in allowlist or "format_type" in allowlist
    ):
        values["format_type"] = parsed.format
    elif parsed.format_type is not None and "format_type" in allowlist:
        values["format_type"] = parsed.format_type
    if parsed.target_minutes is not None and "target_minutes" in allowlist:
        values["target_minutes"] = parsed.target_minutes
    for field in ("style", "audio", "quality"):
        value = getattr(parsed, field)
        if value is not None and field in allowlist:
            values[field] = value
    if not values:
        return replace(profile)
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
                "document_overridable": list(profile.document_overridable),
            }
        )
    except ValidationError as error:
        raise ValueError(f"Invalid profile override: {error}") from error
    return _profile_from_model(model)
