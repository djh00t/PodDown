"""Pydantic contracts for the versioned offline Episode API."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    WithJsonSchema,
    field_validator,
)

ProductionStage = Literal[
    "ingested",
    "prepared",
    "rendering",
    "qa",
    "mastering",
    "packaged",
    "publishing",
    "published",
    "failed",
]
EpisodeResource = Literal["audio", "transcript", "manifest"]


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


class PreviewRequest(_FrozenModel):
    """JSON body for provider-free source preview."""

    source: str = Field(min_length=1)
    profile: str | None = Field(default=None, min_length=1)

    @field_validator("source", "profile")
    @classmethod
    def reject_blank_values(cls, value: str | None) -> str | None:
        """Reject whitespace-only preview inputs without changing source bytes."""
        if value is not None and not value.strip():
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


class ResourceLink(_FrozenModel):
    """Authorized short-lived resource reference returned to API clients."""

    uri: str = Field(min_length=1)
    mime_type: str = Field(min_length=1)


class EpisodeResourceLinkResponse(_FrozenModel):
    """A short-lived, tenant-scoped URL for one immutable episode resource."""

    episode_id: UUID
    episode_version_id: UUID
    tenant_id: UUID
    project_id: UUID
    resource: EpisodeResource
    url: str = Field(min_length=1)
    expires_at: datetime
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("episode_id", "episode_version_id", "tenant_id", "project_id")
    @classmethod
    def require_uuidv7_resource_identities(cls, value: UUID) -> UUID:
        """Keep every resource-link identity in the UUIDv7 domain."""
        if value.version != 7:
            raise ValueError("resource link identifiers must be UUIDv7")
        return value

    @field_validator("expires_at")
    @classmethod
    def require_utc_resource_expiry(cls, value: datetime) -> datetime:
        """Reject offsets and naive timestamps that are not canonical UTC."""
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("resource link expiry must be a UTC datetime")
        return value


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
    resources: list[ResourceLink] = Field(default_factory=list)


class CommandReceipt(_FrozenModel):
    """Replay-safe acknowledgement for one asynchronous command."""

    command_id: UUID
    episode_id: UUID
    idempotency_key: str
    command: Literal["create", "render", "publish"]
    accepted: bool = True
    state: Literal["queued", "dispatched", "running", "completed", "failed"] = "queued"
    workflow_id: str | None = None
    created_at: datetime | None = None


class EpisodeCreateResponse(_FrozenModel):
    """Accepted create response containing the resource and command receipt."""

    episode: EpisodeSummary
    receipt: CommandReceipt


class PreviewResponse(_FrozenModel):
    """Stable source-bound result for a side-effect-free preview."""

    source_sha256: str
    profile_id: str
    source_bytes: int
    block_count: int
    provider_calls: int
    side_effect: Literal["none"] = "none"


class RenderCommandRequest(_FrozenModel):
    """Optional production controls using the frozen render wire contract."""

    provider_route_id: str | None = Field(default=None, min_length=1)
    mode: Literal["deterministic-local", "host-local", "live-provider"] | None = Field(
        default=None,
        validation_alias=AliasChoices("mode", "execution_mode"),
    )
    max_cost: Annotated[Decimal, WithJsonSchema({"type": "string"})] | None = Field(
        default=None,
        gt=0,
        validation_alias=AliasChoices("max_cost", "cost_ceiling"),
    )

    @field_validator("provider_route_id", mode="before")
    @classmethod
    def normalize_provider_route_id(cls, value: object) -> object:
        """Accept UUID objects from existing callers while exposing a string ID."""
        if value is None or isinstance(value, str):
            return value
        if isinstance(value, UUID):
            return str(value)
        raise ValueError("provider_route_id must be a string")

    @field_validator("max_cost", mode="before")
    @classmethod
    def require_decimal_string_max_cost(cls, value: object) -> object:
        """Reject non-string maximum costs before Decimal coercion."""
        if value is None or isinstance(value, str):
            return value
        raise ValueError("max_cost must be a decimal string")

    @property
    def execution_mode(
        self,
    ) -> Literal["deterministic-local", "host-local", "live-provider"] | None:
        """Expose the pre-contract name for internal callers during migration."""
        return self.mode

    @property
    def cost_ceiling(self) -> Decimal | None:
        """Expose the pre-contract name without serializing it on the wire."""
        return self.max_cost


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
    """Tenant-scoped status using the frozen production lifecycle contract."""

    episode_id: UUID
    episode_version_id: UUID
    version: int
    stage: ProductionStage
    progress: float = Field(ge=0, le=1)
    failure: EpisodeFailure | None = None
    workflow_id: str | None = None
    package_manifest_sha256: str | None = None
    publication_id: UUID | None = None

    @field_validator("episode_id", "episode_version_id", "publication_id")
    @classmethod
    def require_uuidv7_status_ids(cls, value: UUID | None) -> UUID | None:
        """Reject non-UUIDv7 public status identities."""
        if value is not None and value.version != 7:
            raise ValueError("status identifiers must be UUIDv7")
        return value

    @property
    def package_manifest_checksum(self) -> str | None:
        """Expose the retired field name for read-only internal compatibility."""
        return self.package_manifest_sha256


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
