"""Validation of canonical Markdown input and PodDown frontmatter."""

import hashlib
from collections.abc import Collection
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from poddown.domain import SourceValidation


class _PodDownMetadata(BaseModel):
    """Strict PodDown-owned portion of otherwise open frontmatter."""

    model_config = ConfigDict(extra="forbid")

    profile: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    format: Literal["narration", "dialogue"] | None = None
    target_minutes: int | None = Field(default=None, ge=1, le=180)
    pronunciation_overrides: (
        dict[
            Annotated[str, StringConstraints(min_length=1)],
            Annotated[str, StringConstraints(min_length=1)],
        ]
        | None
    ) = None


def _frontmatter(source: str) -> dict[object, object]:
    if not source.startswith("---\n"):
        return {}
    boundary = source.find("\n---\n", 4)
    if boundary < 0:
        raise ValueError("Markdown frontmatter is not terminated")
    try:
        parsed = yaml.safe_load(source[4:boundary])
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid YAML frontmatter: {error}") from error
    if not isinstance(parsed, dict):
        raise ValueError("Markdown frontmatter must be an object")
    return parsed


def validate_markdown(
    source: str,
    available_profiles: Collection[str],
    default_profile: str | None = None,
) -> SourceValidation:
    """Validate PodDown metadata without modifying the canonical source."""
    try:
        metadata = _frontmatter(source)
        raw_poddown = metadata.get("poddown", {})
        if not isinstance(raw_poddown, dict):
            raise ValueError("PodDown frontmatter must be an object")
        try:
            poddown = _PodDownMetadata.model_validate(raw_poddown)
        except ValidationError as error:
            extra_keys = [
                item["loc"][-1]
                for item in error.errors()
                if item["type"] == "extra_forbidden"
            ]
            if extra_keys:
                names = ", ".join(sorted(str(key) for key in extra_keys))
                raise ValueError(f"Unknown PodDown key: {names}") from error
            raise ValueError(f"Invalid PodDown metadata: {error}") from error
        profile = poddown.profile or default_profile
        if profile is None:
            raise ValueError("A profile is required after default resolution")
        if profile not in available_profiles:
            raise ValueError(f"Unknown profile: {profile}")
    except ValueError as error:
        return SourceValidation(False, None, (str(error),))

    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return SourceValidation(True, digest, ())
