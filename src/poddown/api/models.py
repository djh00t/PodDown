"""Pydantic contracts for the versioned offline Episode API."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema, field_validator


class _FrozenModel(BaseModel):
    """Keep the demo API response shapes explicit and immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class EpisodeCreateRequest(_FrozenModel):
    """JSON body for source/profile validation."""

    source: str = Field(min_length=1)
    profile: str = Field(min_length=1)

    @field_validator("source", "profile")
    @classmethod
    def reject_blank_values(cls, value: str) -> str:
        """Reject whitespace-only inputs without modifying source fidelity."""
        if not value.strip():
            raise ValueError("value must not be blank")
        return value


class RequestContext(_FrozenModel):
    """Validated tenant/project/idempotency context from demo headers."""

    tenant_id: UUID
    project_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=255)

    @classmethod
    def from_headers(cls, headers: dict[str, str]) -> RequestContext:
        """Parse the required header names with Pydantic validation."""
        return cls(
            tenant_id=headers.get("X-Tenant-ID"),  # type: ignore[arg-type]
            project_id=headers.get("X-Project-ID"),  # type: ignore[arg-type]
            idempotency_key=headers.get("Idempotency-Key"),  # type: ignore[arg-type]
        )


class EpisodeSummary(_FrozenModel):
    """Source-safe immutable episode summary."""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    version: int
    state: str
    profile: str | None = None
    source_sha256: str | None = None
    source_bytes: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CommandReceipt(_FrozenModel):
    """Replay-safe acknowledgement for one asynchronous command."""

    command_id: UUID
    episode_id: UUID
    idempotency_key: str
    command: Literal["create", "render", "publish"]
    accepted: bool = True
    state: Literal["queued", "dispatched", "running", "completed", "failed"] = "queued"
    created_at: datetime | None = None


class EpisodeCreateResponse(_FrozenModel):
    """Accepted create response containing the resource and command receipt."""

    episode: EpisodeSummary
    receipt: CommandReceipt


class RenderCommandRequest(_FrozenModel):
    """Optional production controls for an asynchronous render command."""

    provider_route_id: UUID | None = None
    execution_mode: Literal[
        "deterministic-local", "host-local", "live-provider"
    ] | None = None
    cost_ceiling: (
        Annotated[Decimal, WithJsonSchema({"type": "string"})] | None
    ) = Field(default=None, gt=0)

    @field_validator("cost_ceiling", mode="before")
    @classmethod
    def require_decimal_string_cost_ceiling(cls, value: object) -> object:
        """Reject non-string cost ceilings before Decimal coercion."""
        if value is None or isinstance(value, str):
            return value
        raise ValueError("cost_ceiling must be a decimal string")


class PublishCommandRequest(_FrozenModel):
    """Explicit target and one-time approval for an external publication."""

    target_id: str = Field(min_length=1)
    approval_id: UUID

    @field_validator("target_id")
    @classmethod
    def reject_blank_target_id(cls, value: str) -> str:
        """Reject whitespace-only publication targets without normalizing IDs."""
        if not value.strip():
            raise ValueError("target_id must not be blank")
        return value

    @field_validator("approval_id")
    @classmethod
    def require_uuidv7_approval(cls, value: UUID) -> UUID:
        """Reject approval references that cannot be production UUIDv7 records."""
        if value.version != 7:
            raise ValueError("approval_id must be a UUIDv7")
        return value


class EpisodeFailure(_FrozenModel):
    """Allowlisted failure fields safe to include in a status response."""

    code: str
    stage: str
    status: int
    retriable: bool


class EpisodeStatusResponse(_FrozenModel):
    """Tenant-scoped status without source or credential material."""

    episode_id: UUID
    version: int
    stage: str
    progress: float
    failure: EpisodeFailure | None = None
    workflow_id: str | None = None
    package_manifest_checksum: str | None = None
    publication_id: str | None = None


class ProblemDetail(_FrozenModel):
    """Stable RFC 9457-style problem shape with redacted detail text."""

    type: str
    title: str
    status: int
    code: str
    detail: str

    @classmethod
    def from_exception(
        cls,
        *,
        status: int,
        code: str,
        detail: str,
    ) -> ProblemDetail:
        """Convert arbitrary internal detail to a stable safe message."""
        title = {
            "episode_not_found": "Episode not found",
            "idempotency_conflict": "Idempotency conflict",
            "invalid_source": "Invalid source",
            "invalid_source_encoding": "Invalid source",
            "invalid_profile": "Invalid profile",
            "missing_tenant_id": "Missing tenant context",
            "invalid_tenant_id": "Invalid tenant context",
            "missing_project_id": "Missing project context",
            "invalid_project_id": "Invalid project context",
            "missing_idempotency_key": "Missing idempotency key",
            "invalid_episode_transition": "Invalid episode transition",
            "publish_authorization_required": "Publish authorization required",
        }.get(code, "PodDown request failed")
        safe_detail = {
            "invalid_source": "The request source is invalid.",
            "invalid_source_encoding": "The request source is invalid.",
            "invalid_profile": "The requested profile is invalid.",
        }.get(code, detail)
        return cls(
            type=f"https://poddown.dev/problems/{code}",
            title=title,
            status=status,
            code=code,
            detail=safe_detail,
        )
