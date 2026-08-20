"""Short-lived HMAC-signed resource links bound to tenant and episode scope."""

from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from uuid import UUID

from pydantic import SecretStr


class ResourceLinkError(ValueError):
    """A resource link is malformed, expired, or outside the caller's scope."""


def _uuid7(value: UUID, field: str) -> UUID:
    if not isinstance(value, UUID) or value.version != 7:
        raise ResourceLinkError(f"{field} must be a UUIDv7")
    return value


def _digest(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ResourceLinkError("resource checksum is invalid")
    return value


def _timestamp(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ResourceLinkError("resource-link time must be timezone-aware")
    return value.astimezone(UTC)


def _safe_media_type(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.count("/") != 1
        or any(character.isspace() for character in value)
    ):
        raise ResourceLinkError("resource media type is invalid")
    return value


@dataclass(frozen=True, slots=True)
class ResourceReference:
    """Verified resource metadata returned after signature and scope checks."""

    tenant_id: UUID
    project_id: UUID
    episode_id: UUID
    resource: str
    media_type: str
    sha256: str
    expires_at: datetime


class ResourceLinkSigner:
    """Issue and verify short-lived links without persisting the signing secret."""

    _RESOURCES = frozenset({"audio", "transcript", "manifest"})

    def __init__(
        self,
        secret: SecretStr,
        *,
        base_url: str,
        ttl_seconds: int,
    ) -> None:
        if not isinstance(secret, SecretStr) or len(secret.get_secret_value()) < 32:
            raise ValueError("resource-link secret must contain at least 32 characters")
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("resource-link base URL must be HTTPS without query data")
        if type(ttl_seconds) is not int or not 0 < ttl_seconds <= 3600:
            raise ValueError("resource-link TTL must be between 1 and 3600 seconds")
        self._secret = secret
        self._base_url = base_url.rstrip("/")
        self._base = parsed
        self._ttl_seconds = ttl_seconds

    def to_record(self) -> dict[str, object]:
        """Return link configuration without the signing secret."""
        return {"base_url": self._base_url, "ttl_seconds": self._ttl_seconds}

    def issue(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        episode_id: UUID,
        resource: str,
        media_type: str,
        sha256: str,
        now: datetime,
    ) -> str:
        """Issue one scoped URL with an explicit expiry and content digest."""
        for value, field in (
            (tenant_id, "tenant_id"),
            (project_id, "project_id"),
            (episode_id, "episode_id"),
        ):
            _uuid7(value, field)
        if resource not in self._RESOURCES:
            raise ResourceLinkError("resource kind is unsupported")
        _safe_media_type(media_type)
        _digest(sha256)
        issued_at = _timestamp(now)
        expires = int((issued_at + timedelta(seconds=self._ttl_seconds)).timestamp())
        signature = self._signature(
            tenant_id=tenant_id,
            project_id=project_id,
            episode_id=episode_id,
            resource=resource,
            media_type=media_type,
            sha256=sha256,
            expires=expires,
        )
        path = (
            f"/v1/resources/{tenant_id}/{project_id}/{episode_id}/"
            f"{quote(resource, safe='')}"
        )
        query = urlencode(
            {
                "expires": str(expires),
                "media": media_type,
                "sha256": sha256,
                "sig": signature,
            }
        )
        return f"{self._base_url}{path}?{query}"

    def verify(
        self,
        uri: str,
        *,
        tenant_id: UUID,
        project_id: UUID,
        episode_id: UUID,
        now: datetime,
    ) -> ResourceReference:
        """Verify URL shape, signature, expiry, digest and requested caller scope."""
        for value, field in (
            (tenant_id, "tenant_id"),
            (project_id, "project_id"),
            (episode_id, "episode_id"),
        ):
            _uuid7(value, field)
        parsed = urlsplit(uri)
        if (
            parsed.scheme != self._base.scheme
            or parsed.netloc != self._base.netloc
            or parsed.fragment
        ):
            raise ResourceLinkError("resource link origin is invalid")
        prefix = "/v1/resources/"
        if not parsed.path.startswith(prefix):
            raise ResourceLinkError("resource link path is invalid")
        parts = parsed.path[len(prefix) :].split("/")
        if len(parts) != 4 or parts[3] not in self._RESOURCES:
            raise ResourceLinkError("resource link path is invalid")
        try:
            link_tenant = _uuid7(UUID(parts[0]), "tenant_id")
            link_project = _uuid7(UUID(parts[1]), "project_id")
            link_episode = _uuid7(UUID(parts[2]), "episode_id")
        except ValueError as error:
            raise ResourceLinkError("resource link scope is invalid") from error
        if (link_tenant, link_project, link_episode) != (
            tenant_id,
            project_id,
            episode_id,
        ):
            raise ResourceLinkError("resource link is outside caller scope")
        query = parse_qs(parsed.query, keep_blank_values=True)
        if any(
            len(query.get(name, [])) != 1
            for name in ("expires", "media", "sha256", "sig")
        ):
            raise ResourceLinkError("resource link query is invalid")
        try:
            expires = int(query["expires"][0])
        except (TypeError, ValueError) as error:
            raise ResourceLinkError("resource link expiry is invalid") from error
        media_type = _safe_media_type(query["media"][0])
        digest = _digest(query["sha256"][0])
        signature = query["sig"][0]
        current = _timestamp(now)
        if current.timestamp() >= expires:
            raise ResourceLinkError("resource link is expired")
        expected = self._signature(
            tenant_id=link_tenant,
            project_id=link_project,
            episode_id=link_episode,
            resource=parts[3],
            media_type=media_type,
            sha256=digest,
            expires=expires,
        )
        if not hmac.compare_digest(signature, expected):
            raise ResourceLinkError("resource link signature is invalid")
        return ResourceReference(
            tenant_id=link_tenant,
            project_id=link_project,
            episode_id=link_episode,
            resource=parts[3],
            media_type=media_type,
            sha256=digest,
            expires_at=datetime.fromtimestamp(expires, tz=UTC),
        )

    def _signature(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        episode_id: UUID,
        resource: str,
        media_type: str,
        sha256: str,
        expires: int,
    ) -> str:
        payload = "|".join(
            (
                str(tenant_id),
                str(project_id),
                str(episode_id),
                resource,
                media_type,
                sha256,
                str(expires),
            )
        ).encode("utf-8")
        digest = hmac.new(
            self._secret.get_secret_value().encode("utf-8"),
            payload,
            hashlib.sha256,
        ).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


class ScopedResourceLinkVerifier:
    """Adapt signed links to a verified project scope for agent gateways."""

    def __init__(self, signer: ResourceLinkSigner, *, project_id: UUID) -> None:
        if not isinstance(signer, ResourceLinkSigner):
            raise TypeError("signer must be a ResourceLinkSigner")
        _uuid7(project_id, "project_id")
        self._signer = signer
        self._project_id = project_id

    def verify(self, tenant_id: str, episode_id: str, uri: str) -> bool:
        """Return false for malformed or out-of-scope links without leaking detail."""
        try:
            self._signer.verify(
                uri,
                tenant_id=_uuid7(UUID(tenant_id), "tenant_id"),
                project_id=self._project_id,
                episode_id=_uuid7(UUID(episode_id), "episode_id"),
                now=datetime.now(UTC),
            )
        except (ResourceLinkError, TypeError, ValueError):
            return False
        return True


__all__ = [
    "ResourceLinkError",
    "ResourceLinkSigner",
    "ResourceReference",
    "ScopedResourceLinkVerifier",
]
