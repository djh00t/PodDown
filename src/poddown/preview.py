"""Deterministic, provider-free Markdown preview application boundary."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import cast

import yaml

from poddown.content.source import snapshot_source
from poddown.intake import validate_markdown

_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class PreviewValidationError(ValueError):
    """Raised when an offline preview cannot validate its input."""

    def __init__(self, errors: tuple[str, ...]) -> None:
        if not errors or any(
            not isinstance(error, str) or not error for error in errors
        ):
            raise ValueError("preview errors must contain non-empty messages")
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass(frozen=True)
class PreviewResult:
    """Stable evidence returned by one local preview."""

    source_sha256: str
    profile_id: str
    source_bytes: int
    block_count: int
    provider_calls: int = 0


def _config_mapping(
    value: Mapping[str, object] | None, name: str
) -> Mapping[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise PreviewValidationError((f"Invalid {name} configuration",))
    return value


def _profile_value(config: Mapping[str, object]) -> str | None:
    value = config.get("profile")
    if value is None:
        return None
    if not isinstance(value, str) or not _PROFILE_ID.fullmatch(value):
        raise PreviewValidationError(("Invalid profile configuration",))
    return value


def _configured_profiles(config: Mapping[str, object]) -> set[str]:
    raw = config.get("profiles")
    if raw is None:
        return set()
    values: Iterable[object]
    if isinstance(raw, Mapping):
        values = raw.keys()
    elif isinstance(raw, (tuple, list, set, frozenset)):
        values = raw
    else:
        raise PreviewValidationError(("Invalid profile configuration",))
    profiles: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not _PROFILE_ID.fullmatch(value):
            raise PreviewValidationError(("Invalid profile configuration",))
        profiles.add(value)
    return profiles


def _document_profile(source: str) -> str | None:
    if source.startswith("---\r\n"):
        prefix = 5
        boundary = source.find("\r\n---\r\n", prefix)
    elif source.startswith("---\n"):
        prefix = 4
        boundary = source.find("\n---\n", prefix)
    else:
        return None
    if boundary < 0:
        return None
    try:
        parsed = yaml.safe_load(source[prefix:boundary])
    except yaml.YAMLError:
        return None
    if not isinstance(parsed, Mapping):
        return None
    poddown = parsed.get("poddown")
    if not isinstance(poddown, Mapping):
        return None
    profile = poddown.get("profile")
    return profile if isinstance(profile, str) else None


def _stable_validation_errors(errors: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        "Invalid YAML frontmatter"
        if error.startswith("Invalid YAML frontmatter:")
        else error
        for error in errors
    )


def _resolved_profile(
    source: str,
    flag_profile: str | None,
    project_config: Mapping[str, object],
    user_config: Mapping[str, object],
) -> tuple[str, set[str], str | None]:
    if flag_profile is not None and not _PROFILE_ID.fullmatch(flag_profile):
        raise PreviewValidationError(("Invalid profile configuration",))
    project_profile = _profile_value(project_config)
    user_profile = _profile_value(user_config)
    document_profile = _document_profile(source)
    profiles = _configured_profiles(project_config) | _configured_profiles(user_config)
    profiles.update(
        profile
        for profile in (flag_profile, project_profile, user_profile)
        if profile is not None
    )
    if document_profile is not None and not _PROFILE_ID.fullmatch(document_profile):
        raise PreviewValidationError(("Invalid profile configuration",))
    selected = (
        flag_profile or document_profile or project_profile or user_profile or "default"
    )
    profiles.add("default")
    default_profile = project_profile or user_profile or "default"
    return selected, profiles, default_profile


def preview_markdown(
    source: bytes,
    *,
    source_name: str,
    flag_profile: str | None,
    project_config: Mapping[str, object] | None,
    user_config: Mapping[str, object] | None,
) -> PreviewResult:
    """Validate exact UTF-8 Markdown and return deterministic local evidence."""
    if not isinstance(source, bytes):
        raise PreviewValidationError(("Source must be UTF-8 bytes",))
    if not isinstance(source_name, str) or not source_name:
        raise PreviewValidationError(("Source name must be non-empty",))
    try:
        decoded = source.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PreviewValidationError(("Source is not valid UTF-8",)) from error

    project = _config_mapping(project_config, "project")
    user = _config_mapping(user_config, "user")
    selected, available_profiles, default_profile = _resolved_profile(
        decoded, flag_profile, project, user
    )
    validation = validate_markdown(
        decoded,
        available_profiles,
        default_profile=default_profile,
        profile_override=flag_profile,
    )
    if not validation.accepted:
        raise PreviewValidationError(_stable_validation_errors(validation.errors))

    try:
        snapshot = snapshot_source(decoded)
    except (TypeError, ValueError) as error:
        raise PreviewValidationError((str(error),)) from error
    digest = sha256(source).hexdigest()
    if validation.source_sha256 != sha256(decoded.encode("utf-8")).hexdigest():
        raise PreviewValidationError(("Source validation digest is inconsistent",))
    if not digest:
        raise PreviewValidationError(("Source digest is invalid",))
    return PreviewResult(
        source_sha256=digest,
        profile_id=selected,
        source_bytes=len(source),
        block_count=len(snapshot.blocks),
        provider_calls=0,
    )


def render_preview_json(result: PreviewResult) -> bytes:
    """Serialize preview evidence as compact, sorted UTF-8 JSON."""
    if not isinstance(result, PreviewResult):
        raise PreviewValidationError(("Preview result is malformed",))
    return json.dumps(
        cast(dict[str, object], asdict(result)),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


__all__ = [
    "PreviewResult",
    "PreviewValidationError",
    "preview_markdown",
    "render_preview_json",
]
