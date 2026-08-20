"""Offline-safe immutable publication contracts and replaceable adapters."""

from __future__ import annotations

import contextlib
import json
import math
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from uuid import UUID

from uuid6 import uuid7

from poddown.artifacts import ArtifactStore
from poddown.object_storage import (
    ObjectIntegrityError,
    ObjectNotFound,
    ObjectRef,
    ObjectStore,
    ObjectStoreDiscovery,
    storage_key_for,
)
from poddown.packages import EpisodePackage, package_sha256_for_artifacts

if TYPE_CHECKING:
    from poddown.publication_repository import (
        DurablePublicationAttempt,
        PublicationAttemptRequest,
    )


class PublishingError(ValueError):
    """Base publication contract error."""


class PublishingValidationError(PublishingError):
    """Publication input or package evidence is invalid."""


class PublishingAuthorizationError(PublishingError):
    """An operation lacks its explicit authorization decision."""


class PublicationConflictError(PublishingError, RuntimeError):
    """An idempotency key or immutable publication conflicts with prior evidence."""


class PublicationOutcome(StrEnum):
    """Explicit adapter outcome semantics for a failed dispatch."""

    KNOWN_NOT_APPLIED = "known_not_applied"
    COMPENSATED = "compensated"
    UNCERTAIN = "uncertain"


_SENSITIVE_ERROR_FIELDS = frozenset(
    {
        "access_token",
        "api-key",
        "api_key",
        "auth",
        "authorization",
        "client_secret",
        "credential",
        "password",
        "secret",
        "token",
    }
)
_CREDENTIAL_CONTAINER_PARTS = frozenset(
    {
        "auth",
        "authentication",
        "body",
        "credential",
        "credentials",
        "form",
        "headers",
        "header",
        "oauth",
        "params",
        "password",
        "query",
        "secret",
        "secrets",
        "token",
        "tokens",
    }
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r'(?P<key>"?(?:access_token|api[-_]?key|auth(?:orization)?|client_secret|'
    r'credential|password|secret|token)"?)'
    r"(?P<separator>\s*[:=]\s*)"
    r"""(?P<value>"[^"]*"|'[^']*'|[^\s,&}]+)""",
    re.IGNORECASE,
)
_BEARER_TOKEN = re.compile(r"\bBearer\s+\S+", re.IGNORECASE)
_AUTHORIZATION = re.compile(r"\bauthorization\s*:\s*(?:bearer\s+)?\S+", re.IGNORECASE)
_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_MAX_ERROR_EVIDENCE_LENGTH = 256
_JSON_DECODER = json.JSONDecoder()


def _normalized_field_name(value: object) -> str:
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(value))
    return re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()


def _field_parts(value: object) -> set[str]:
    return set(_normalized_field_name(value).split("_"))


def _is_sensitive_field(value: object) -> bool:
    normalized = _normalized_field_name(value)
    return normalized in _SENSITIVE_ERROR_FIELDS or bool(
        _field_parts(value)
        & {
            "access",
            "api",
            "auth",
            "authorization",
            "credential",
            "credentials",
            "key",
            "password",
            "secret",
            "secrets",
            "token",
            "tokens",
        }
    )


def _is_credential_container(value: object) -> bool:
    return bool(_field_parts(value) & _CREDENTIAL_CONTAINER_PARTS)


def _redact_error_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]"
            if _is_sensitive_field(key) or _is_credential_container(key)
            else _redact_error_value(nested)
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [_redact_error_value(item) for item in value]
    if isinstance(value, str):
        return _sanitize_text(value)
    return value


def _structured_value_end(message: str, start: int) -> int:
    if start >= len(message):
        return start
    if message[start] in {'"', "'"}:
        closing_quote = message[start]
        escaped = False
        for index in range(start + 1, len(message)):
            character = message[index]
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == closing_quote:
                return index + 1
        return len(message)
    if message[start] in "[{":
        opening = message[start]
        closing = "]" if opening == "[" else "}"
        stack = [closing]
        active_quote: str | None = None
        escaped = False
        for index in range(start + 1, len(message)):
            character = message[index]
            if active_quote is not None:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == active_quote:
                    active_quote = None
                continue
            if character in {'"', "'"}:
                active_quote = character
            elif character in "[{":
                stack.append("]" if character == "[" else "}")
            elif character in "]}":
                if not stack or character != stack[-1]:
                    return index + 1
                stack.pop()
                if not stack:
                    return index + 1
        return len(message)
    index = start
    while (
        index < len(message)
        and not message[index].isspace()
        and message[index] not in ",;"
    ):
        index += 1
    return index


_TEXT_FIELD_ASSIGNMENT = re.compile(
    r'(?P<key>"?[A-Za-z][A-Za-z0-9_-]*"?)(?P<separator>\s*[:=]\s*)'
)


def _redact_text_containers(message: str) -> str:
    output: list[str] = []
    cursor = 0
    for match in _TEXT_FIELD_ASSIGNMENT.finditer(message):
        if match.start() < cursor:
            continue
        key = match.group("key").strip('"')
        if not _is_credential_container(key):
            continue
        value_start = match.end()
        value_end = _structured_value_end(message, value_start)
        output.append(message[cursor:value_start])
        output.append("[REDACTED]")
        cursor = value_end
    output.append(message[cursor:])
    return "".join(output)


def _sanitize_plain_text(message: str) -> str:
    sanitized = _redact_text_containers(message)
    sanitized = _AUTHORIZATION.sub("Authorization: [REDACTED]", sanitized)
    sanitized = _BEARER_TOKEN.sub("Bearer [REDACTED]", sanitized)
    sanitized = _URL.sub("[REDACTED_URL]", sanitized)
    return _SENSITIVE_ASSIGNMENT.sub(
        lambda match: f"{match.group('key')}{match.group('separator')}[REDACTED]",
        sanitized,
    )


def _sanitize_text(message: str) -> str:
    start = len(message) - len(message.lstrip())
    try:
        parsed, end = _JSON_DECODER.raw_decode(message, start)
    except json.JSONDecodeError:
        return _sanitize_plain_text(message)
    if isinstance(parsed, str):
        nested = parsed.strip()
        if nested and nested != message and nested[0] in "[{":
            return _sanitize_text(nested)
        return _sanitize_plain_text(message)
    serialized = json.dumps(
        _redact_error_value(parsed), sort_keys=True, separators=(",", ":")
    )
    suffix = message[end:]
    if suffix.strip():
        return serialized + _sanitize_plain_text(suffix)
    return serialized


def sanitize_publication_error(error: BaseException | str) -> str:
    """Redact structured credential fields before error storage or exposure."""

    return _sanitize_text(str(error))[:_MAX_ERROR_EVIDENCE_LENGTH]


class PublicationAdapterError(PublishingError, RuntimeError):
    """Safe adapter failure carrying an explicit provider outcome."""

    def __init__(
        self,
        message: str,
        *,
        outcome: PublicationOutcome | str,
        external_reference: str | None = None,
        status_code: int | None = None,
    ) -> None:
        self.outcome = PublicationOutcome(outcome)
        self.external_reference = external_reference
        self.status_code = status_code
        super().__init__(sanitize_publication_error(message))

    @classmethod
    def from_http_error(cls, error: HTTPError) -> PublicationAdapterError:
        """Translate an HTTP adapter failure into an explicit safe outcome."""

        outcome = (
            PublicationOutcome.KNOWN_NOT_APPLIED
            if 400 <= error.code < 500 and error.code != 408
            else PublicationOutcome.UNCERTAIN
        )
        return cls(str(error), outcome=outcome, status_code=error.code)


class PublicationUncertainOutcomeError(PublicationAdapterError):
    """The provider may have accepted a mutation without returning a response."""

    def __init__(self, message: str) -> None:
        super().__init__(message, outcome=PublicationOutcome.UNCERTAIN)


class PublicationTransportError(PublishingError, RuntimeError):
    """A provider transport failure safe to classify without response leakage."""


_SAFE_TARGET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True, slots=True)
class DisclosurePolicy:
    """Disclosure decisions captured alongside a publication."""

    spoken: bool = False
    show_notes: bool = False
    platform: bool = False


@dataclass(frozen=True, slots=True)
class PublicationAuthorization:
    """Explicit authorization, bound to one operation and actor."""

    actor_id: str
    decision_id: str
    reason: str
    operation: str = "publish"

    def __post_init__(self) -> None:
        if self.operation not in {"publish", "update", "delete"}:
            raise PublishingValidationError("publication operation is invalid")
        if self.operation != "publish" and not self.decision_id:
            raise PublishingAuthorizationError("separate authorization is required")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (self.actor_id, self.decision_id, self.reason)
        ):
            raise PublishingValidationError("authorization provenance is incomplete")

    def to_dict(self) -> dict[str, str]:
        return {
            "actor_id": self.actor_id,
            "decision_id": self.decision_id,
            "reason": self.reason,
            "operation": self.operation,
        }


@dataclass(frozen=True, slots=True)
class PublicationTarget:
    """Provider-neutral target metadata; secrets are references, never values."""

    tenant_id: UUID
    project_id: UUID
    target_id: str
    kind: str
    secret_ref: str
    show_id: str
    feed_url: str
    disclosure: DisclosurePolicy
    visibility: str = "public"
    update_policy: str = "immutable"

    def __post_init__(self) -> None:
        if self.tenant_id.version != 7 or self.project_id.version != 7:
            raise PublishingValidationError("publication scope must use UUIDv7")
        if not self.secret_ref.startswith("secret://"):
            raise PublishingValidationError(
                "publication target requires a secret reference"
            )
        if self.kind not in {"filesystem", "s3", "rss", "transistor"}:
            raise PublishingValidationError("publication target kind is unsupported")
        if _SAFE_TARGET.fullmatch(self.target_id) is None or not self.show_id:
            raise PublishingValidationError("publication target identity is required")


@dataclass(frozen=True, slots=True)
class PublicationActivityRequest:
    """JSON-safe Temporal input for one explicit publication activity."""

    tenant_id: UUID
    project_id: UUID
    episode_id: UUID
    package: EpisodePackage
    target: PublicationTarget
    authorization: PublicationAuthorization
    idempotency_key: str

    def __post_init__(self) -> None:
        for name, value in (
            ("tenant_id", self.tenant_id),
            ("project_id", self.project_id),
            ("episode_id", self.episode_id),
        ):
            if not isinstance(value, UUID) or value.version != 7:
                raise PublishingValidationError(f"{name} must be UUIDv7")
        if not isinstance(self.package, EpisodePackage):
            raise PublishingValidationError("publication package is malformed")
        if not isinstance(self.target, PublicationTarget):
            raise PublishingValidationError("publication target is malformed")
        if (
            self.target.tenant_id != self.tenant_id
            or self.target.project_id != self.project_id
        ):
            raise PublishingValidationError("publication target scope is inconsistent")
        if not isinstance(self.authorization, PublicationAuthorization):
            raise PublishingValidationError("publication authorization is malformed")
        if (
            not isinstance(self.idempotency_key, str)
            or not self.idempotency_key.strip()
        ):
            raise PublishingValidationError(
                "publication activity idempotency key is required"
            )

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> PublicationActivityRequest:
        """Decode the complete, secret-reference-only activity payload."""
        if not isinstance(payload, Mapping):
            raise PublishingValidationError("publication activity payload is malformed")
        raw_payload: Mapping[str, object] = payload
        nested = payload.get("payload")
        if isinstance(nested, Mapping):
            raw_payload = nested

        def uuid7_value(name: str, value: object) -> UUID:
            try:
                parsed = UUID(str(value))
            except (AttributeError, TypeError, ValueError) as error:
                raise PublishingValidationError(f"{name} is invalid") from error
            if parsed.version != 7:
                raise PublishingValidationError(f"{name} must be UUIDv7")
            return parsed

        package_value = raw_payload.get("package")
        target_value = raw_payload.get("target")
        authorization_value = raw_payload.get("authorization")
        if not isinstance(package_value, Mapping):
            raise PublishingValidationError("publication package is required")
        if not isinstance(target_value, Mapping):
            raise PublishingValidationError("publication target is required")
        if not isinstance(authorization_value, Mapping):
            raise PublishingValidationError("publication authorization is required")
        idempotency_key = raw_payload.get("idempotency_key")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise PublishingValidationError(
                "publication activity idempotency key is required"
            )
        disclosure_value = target_value.get("disclosure", {})
        if not isinstance(disclosure_value, Mapping):
            raise PublishingValidationError("publication disclosure is malformed")
        try:
            target = PublicationTarget(
                tenant_id=uuid7_value("target tenant_id", target_value["tenant_id"]),
                project_id=uuid7_value("target project_id", target_value["project_id"]),
                target_id=target_value["target_id"],
                kind=target_value["kind"],
                secret_ref=target_value["secret_ref"],
                show_id=target_value["show_id"],
                feed_url=target_value["feed_url"],
                disclosure=DisclosurePolicy(
                    spoken=disclosure_value.get("spoken", False),
                    show_notes=disclosure_value.get("show_notes", False),
                    platform=disclosure_value.get("platform", False),
                ),
                visibility=target_value.get("visibility", "public"),
                update_policy=target_value.get("update_policy", "immutable"),
            )
            authorization = PublicationAuthorization(
                actor_id=authorization_value["actor_id"],
                decision_id=authorization_value["decision_id"],
                reason=authorization_value["reason"],
                operation=authorization_value.get("operation", "publish"),
            )
            package = EpisodePackage.from_dict(package_value)
            return cls(
                tenant_id=uuid7_value("tenant_id", raw_payload["tenant_id"]),
                project_id=uuid7_value("project_id", raw_payload["project_id"]),
                episode_id=uuid7_value("episode_id", raw_payload["episode_id"]),
                package=package,
                target=target,
                authorization=authorization,
                idempotency_key=idempotency_key,
            )
        except KeyError as error:
            raise PublishingValidationError(
                "publication activity payload is incomplete"
            ) from error
        except (AttributeError, TypeError, ValueError) as error:
            if isinstance(error, PublishingError):
                raise
            raise PublishingValidationError(
                "publication activity payload is malformed"
            ) from error


@dataclass(frozen=True, slots=True)
class PublicationReceipt:
    """Immutable, provenance-bound result of one publication attempt."""

    publication_id: str
    tenant_id: UUID
    project_id: UUID
    episode_version_id: str
    target_id: str
    idempotency_key: str
    package_sha256: str
    external_id: str
    status: str
    authorization: PublicationAuthorization
    disclosure: DisclosurePolicy
    provenance: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(self.package_sha256) != 64 or any(
            c not in "0123456789abcdef" for c in self.package_sha256
        ):
            raise PublishingValidationError("publication package checksum is invalid")
        if self.status not in {"published", "resumed", "compensated"}:
            raise PublishingValidationError("publication receipt status is invalid")
        if self.authorization.operation != "publish":
            raise PublishingAuthorizationError(
                "publication receipt requires publish authorization"
            )
        object.__setattr__(self, "provenance", _freeze_mapping(self.provenance))

    def to_dict(self) -> dict[str, object]:
        return {
            "publication_id": self.publication_id,
            "tenant_id": str(self.tenant_id),
            "project_id": str(self.project_id),
            "episode_version_id": self.episode_version_id,
            "target_id": self.target_id,
            "idempotency_key": self.idempotency_key,
            "package_sha256": self.package_sha256,
            "external_id": self.external_id,
            "status": self.status,
            "authorization": self.authorization.to_dict(),
            "disclosure": {
                "spoken": self.disclosure.spoken,
                "show_notes": self.disclosure.show_notes,
                "platform": self.disclosure.platform,
            },
            "provenance": _thaw(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class PublicationMutationReceipt:
    """Auditable result for a separately authorized update or delete."""

    publication_id: str
    tenant_id: UUID
    project_id: UUID
    target_id: str
    operation: str
    idempotency_key: str
    status: str
    authorization: PublicationAuthorization
    provenance: dict[str, object]

    def __post_init__(self) -> None:
        if self.operation not in {"update", "delete"}:
            raise PublishingValidationError("publication mutation is invalid")
        if self.status not in {"updated", "deleted", "unsupported"}:
            raise PublishingValidationError("publication mutation status is invalid")
        if self.authorization.operation != self.operation:
            raise PublishingAuthorizationError(
                "separate mutation authorization is required"
            )
        object.__setattr__(self, "provenance", _freeze_mapping(self.provenance))

    def to_dict(self) -> dict[str, object]:
        return {
            "publication_id": self.publication_id,
            "tenant_id": str(self.tenant_id),
            "project_id": str(self.project_id),
            "target_id": self.target_id,
            "operation": self.operation,
            "idempotency_key": self.idempotency_key,
            "status": self.status,
            "authorization": self.authorization.to_dict(),
            "provenance": _thaw(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class PublicationHttpRequest:
    """Minimal sync HTTP request contract for external publication adapters."""

    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    form: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not isinstance(self.method, str) or not self.method.strip():
            raise PublishingValidationError("publication HTTP method is required")
        if not isinstance(self.url, str) or not self.url.strip():
            raise PublishingValidationError("publication HTTP URL is required")
        if not isinstance(self.headers, Mapping) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in self.headers.items()
        ):
            raise PublishingValidationError("publication HTTP headers are invalid")
        if not isinstance(self.form, Mapping) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in self.form.items()
        ):
            raise PublishingValidationError("publication HTTP form is invalid")
        if not isinstance(self.body, bytes):
            raise PublishingValidationError("publication HTTP body is invalid")
        if (
            not isinstance(self.timeout_seconds, (int, float))
            or isinstance(self.timeout_seconds, bool)
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
            or self.timeout_seconds > 300
        ):
            raise PublishingValidationError("publication HTTP timeout is invalid")


@dataclass(frozen=True, slots=True)
class PublicationHttpResponse:
    """Minimal sync HTTP response contract for external publication adapters."""

    status: int
    headers: Mapping[str, str]
    body: bytes

    def __post_init__(self) -> None:
        if type(self.status) is not int or not 100 <= self.status <= 599:
            raise PublishingValidationError("publication HTTP status is invalid")
        if not isinstance(self.headers, Mapping) or not isinstance(self.body, bytes):
            raise PublishingValidationError("publication HTTP response is invalid")


class PublicationHttpTransport(Protocol):
    """Injected sync transport; tests never need network access."""

    def request(self, request: PublicationHttpRequest) -> PublicationHttpResponse:
        """Execute one request without retries or hidden mutation."""


class UrllibPublicationTransport:
    """Standard-library transport used only when an explicit live adapter is wired."""

    def request(self, request: PublicationHttpRequest) -> PublicationHttpResponse:
        """Execute one bounded request and classify network failures safely."""
        if not isinstance(request, PublicationHttpRequest):
            raise TypeError("request must be a PublicationHttpRequest")
        body = request.body
        headers = dict(request.headers)
        if request.form:
            if body:
                raise PublishingValidationError(
                    "publication HTTP form and body are mutually exclusive"
                )
            body = urlencode(request.form).encode("utf-8")
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        outgoing = Request(
            request.url,
            data=body or None,
            headers=headers,
            method=request.method.upper(),
        )
        try:
            with urlopen(outgoing, timeout=request.timeout_seconds) as response:
                return PublicationHttpResponse(
                    int(response.status),
                    {str(key): str(value) for key, value in response.headers.items()},
                    response.read(),
                )
        except HTTPError as error:
            return PublicationHttpResponse(
                int(error.code),
                {str(key): str(value) for key, value in error.headers.items()},
                error.read(),
            )
        except (TimeoutError, URLError, OSError) as error:
            raise PublicationTransportError(
                "publication provider transport failed"
            ) from error


class PublicationAdapter(Protocol):
    """Provider-neutral adapter boundary."""

    def publish(
        self,
        package: EpisodePackage,
        target: PublicationTarget,
        artifacts: dict[str, bytes],
    ) -> str:
        """Publish exact package bytes and return an external identifier."""


class PublicationReceiptRepository(Protocol):
    """Durable receipt and attempt boundary used by publishing services."""

    def lookup(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        episode_version_id: str,
        target_id: str,
        idempotency_key: str,
    ) -> DurablePublicationAttempt | None:
        """Read an attempt without requiring package artifact access."""

    def begin(self, value: PublicationAttemptRequest) -> DurablePublicationAttempt:
        """Begin one safe attempt or return a replayable successful receipt."""

    def succeed(
        self, attempt: DurablePublicationAttempt, receipt: PublicationReceipt
    ) -> DurablePublicationAttempt:
        """Persist one successful receipt for a pending attempt."""

    def fail(
        self, attempt: DurablePublicationAttempt, error: str
    ) -> DurablePublicationAttempt:
        """Record a known provider failure that may safely be retried."""

    def mark_uncertain(
        self,
        attempt: DurablePublicationAttempt,
        error: str,
        *,
        external_reference: str | None = None,
    ) -> DurablePublicationAttempt:
        """Record an outcome that must not be retried blindly."""


class Publisher(Protocol):
    """Public port for immutable publication."""

    def publish(
        self,
        package: EpisodePackage,
        target: PublicationTarget,
        authorization: PublicationAuthorization,
        idempotency_key: str,
    ) -> PublicationReceipt:
        """Publish or replay one immutable package."""


class PublicationReceiptStore(Protocol):
    """Durable port for immutable publication receipts."""

    def save(self, receipt: PublicationReceipt) -> PublicationReceipt:
        """Insert or replay one receipt, rejecting immutable conflicts."""

    def get_by_idempotency(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        target_id: str,
        idempotency_key: str,
    ) -> PublicationReceipt | None:
        """Read a receipt in one tenant and project scope."""


def _package_identity(package: EpisodePackage) -> str:
    return sha256(
        json.dumps(package.to_dict(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _durable_publication_snapshot(
    target: PublicationTarget, package_identity: str
) -> Mapping[str, object]:
    return {
        **_target_snapshot(target),
        "package_identity": package_identity,
    }


def _adapter_outcome(error: BaseException) -> PublicationOutcome:
    """Read explicit adapter semantics; untyped errors fail closed."""

    candidate = getattr(error, "outcome", None)
    if not isinstance(candidate, str):
        return PublicationOutcome.UNCERTAIN
    try:
        return PublicationOutcome(candidate)
    except ValueError:
        return PublicationOutcome.UNCERTAIN


def _record_known_failure(
    attempt: PublicationAttempt,
    repository: PublicationReceiptRepository | None,
    durable_attempt: DurablePublicationAttempt | None,
    error: BaseException,
) -> str:
    safe_error = sanitize_publication_error(error)
    attempt.state = "failed"
    attempt.retryable = True
    attempt.failure = safe_error
    if repository is not None and durable_attempt is not None:
        repository.fail(durable_attempt, safe_error)
    return safe_error


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    def freeze(item: object) -> object:
        if isinstance(item, Mapping):
            return MappingProxyType(
                {str(key): freeze(nested) for key, nested in item.items()}
            )
        if isinstance(item, (list, tuple)):
            return tuple(freeze(nested) for nested in item)
        return item

    frozen = freeze(value)
    if not isinstance(frozen, Mapping):
        raise PublishingValidationError("publication provenance must be a mapping")
    return frozen


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _target_snapshot(target: PublicationTarget) -> dict[str, object]:
    return {
        "target_id": target.target_id,
        "kind": target.kind,
        "show_id": target.show_id,
        "feed_url": target.feed_url,
        "visibility": target.visibility,
        "update_policy": target.update_policy,
        "disclosure": {
            "spoken": target.disclosure.spoken,
            "show_notes": target.disclosure.show_notes,
            "platform": target.disclosure.platform,
        },
    }


def _read_artifacts(store: ArtifactStore, package: EpisodePackage) -> dict[str, bytes]:
    _validate_package(package)
    artifacts: dict[str, bytes] = {}
    for reference in package.files:
        data = store.read(reference)
        if (
            sha256(data).hexdigest() != reference.sha256
            or len(data) != reference.byte_count
        ):
            raise PublishingValidationError("package artifact checksum does not match")
        artifacts[reference.name] = data
    if "episode.mp3" not in artifacts and "episode.wav" not in artifacts:
        raise PublishingValidationError("package has no publishable audio")
    return artifacts


def _validate_package(package: EpisodePackage) -> None:
    if package.provenance.qa != "pass":
        raise PublishingValidationError("only QA-passed packages can publish")
    if package.provenance.critical_token_accuracy != 1.0:
        raise PublishingValidationError("only fully verified packages can publish")
    try:
        EpisodePackage.from_dict(package.to_dict())
    except ValueError as error:
        raise PublishingValidationError("package manifest is invalid") from error


@dataclass
class PublicationAttempt:
    """Process-local retry state for one scoped publication key."""

    tenant_id: UUID
    project_id: UUID
    idempotency_key: str
    state: str = "pending"
    retryable: bool = True
    failure: str | None = None
    receipt: PublicationReceipt | None = None


class FilesystemPublicationAdapter:
    """Write exact package bytes to a deterministic local publication root."""

    def __init__(self, root: Path, *, fail_after: int | None = None) -> None:
        self.root = Path(root)
        self.fail_after = fail_after

    @staticmethod
    def _destination(
        root: Path, target: PublicationTarget, episode_version_id: str
    ) -> Path:
        try:
            episode_identity = str(UUID(episode_version_id))
        except (TypeError, ValueError) as error:
            raise PublishingValidationError(
                "publication episode identity is invalid"
            ) from error
        return (
            root
            / "tenants"
            / str(target.tenant_id)
            / "projects"
            / str(target.project_id)
            / target.target_id
            / episode_identity
        )

    def publish(
        self,
        package: EpisodePackage,
        target: PublicationTarget,
        artifacts: dict[str, bytes],
    ) -> str:
        destination = self._destination(self.root, target, package.episode_version_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".publishing-", dir=destination.parent))
        try:
            for index, (name, data) in enumerate(artifacts.items(), 1):
                (staging / name).write_bytes(data)
                if self.fail_after is not None and index >= self.fail_after:
                    raise RuntimeError("staged publication failure")
            if destination.exists():
                for path in staging.iterdir():
                    existing = destination / path.name
                    if existing.read_bytes() != path.read_bytes():
                        raise PublishingValidationError(
                            "published bytes conflict with package"
                        )
                shutil.rmtree(staging)
            else:
                staging.rename(destination)
        except Exception as error:
            shutil.rmtree(staging, ignore_errors=True)
            with contextlib.suppress(OSError):
                destination.parent.rmdir()
            with contextlib.suppress(OSError):
                destination.parent.parent.rmdir()
            with contextlib.suppress(OSError):
                destination.parent.parent.parent.rmdir()
            with contextlib.suppress(OSError):
                destination.parent.parent.parent.parent.rmdir()
            with contextlib.suppress(OSError):
                destination.parent.parent.parent.parent.parent.rmdir()
            raise PublicationAdapterError(
                str(error), outcome=PublicationOutcome.COMPENSATED
            ) from error
        return f"filesystem:{target.target_id}:{package.episode_version_id}"

    def update(self, receipt: PublicationReceipt) -> str:
        destination = self._destination(
            self.root,
            PublicationTarget(
                receipt.tenant_id,
                receipt.project_id,
                receipt.target_id,
                "filesystem",
                "secret://internal",
                "show",
                "https://invalid",
                DisclosurePolicy(),
            ),
            receipt.episode_version_id,
        )
        if not destination.exists():
            raise PublishingValidationError("filesystem publication is unavailable")
        (destination / ".publication-update.json").write_text(
            json.dumps(receipt.to_dict(), sort_keys=True, separators=(",", ":"))
        )
        return f"filesystem-updated:{receipt.publication_id}"

    def delete(self, receipt: PublicationReceipt) -> str:
        destination = self._destination(
            self.root,
            PublicationTarget(
                receipt.tenant_id,
                receipt.project_id,
                receipt.target_id,
                "filesystem",
                "secret://internal",
                "show",
                "https://invalid",
                DisclosurePolicy(),
            ),
            receipt.episode_version_id,
        )
        shutil.rmtree(destination, ignore_errors=True)
        return f"filesystem-deleted:{receipt.publication_id}"


class S3CompatiblePublicationAdapter:
    """Use the existing tenant-scoped ObjectStore with S3-compatible semantics."""

    def __init__(self, object_store: ObjectStore) -> None:
        self.object_store = object_store
        self.references: dict[tuple[str, str], ObjectRef] = {}
        self.completed: set[str] = set()
        self.fail_after: int | None = None
        self._lock = RLock()
        self.staged_references: dict[str, dict[str, ObjectRef]] = {}
        self.final_references: dict[str, dict[str, ObjectRef]] = {}

    def publish(
        self,
        package: EpisodePackage,
        target: PublicationTarget,
        artifacts: dict[str, bytes],
    ) -> str:
        attempt_key = (
            f"{target.tenant_id}:{target.project_id}:{target.target_id}:"
            f"{package.episode_version_id}"
        )
        with self._lock:
            staged: dict[str, ObjectRef] = {}
            self.staged_references[attempt_key] = staged
            try:
                for index, (name, data) in enumerate(artifacts.items(), 1):
                    digest = sha256(data).hexdigest()
                    candidate = ObjectRef(
                        target.tenant_id,
                        target.project_id,
                        name,
                        "audio/mpeg"
                        if name == "episode.mp3"
                        else "application/octet-stream",
                        len(data),
                        digest,
                        storage_key_for(target.tenant_id, target.project_id, digest),
                    )
                    try:
                        self.object_store.read(
                            target.tenant_id, target.project_id, candidate
                        )
                        reference = candidate
                    except ObjectNotFound:
                        reference = self.object_store.put(
                            target.tenant_id,
                            target.project_id,
                            name=name,
                            media_type="audio/mpeg"
                            if name == "episode.mp3"
                            else "application/octet-stream",
                            data=data,
                        )
                    except ObjectIntegrityError:
                        raise
                    staged[name] = reference
                    if self.fail_after is not None and index >= self.fail_after:
                        raise RuntimeError("object publication failure")
                self.final_references[target.target_id] = dict(staged)
                self.references.update(
                    {(target.target_id, name): ref for name, ref in staged.items()}
                )
                self.completed.add(target.target_id)
            except Exception as error:
                self.staged_references.pop(attempt_key, None)
                self.final_references.pop(target.target_id, None)
                self.completed.discard(target.target_id)
                for name in artifacts:
                    self.references.pop((target.target_id, name), None)
                raise error
            self.staged_references.pop(attempt_key, None)
        return f"s3:{target.target_id}:{package.episode_version_id}"

    def update(self, receipt: PublicationReceipt) -> str:
        raise PublishingValidationError("S3-compatible update is unsupported offline")

    def delete(self, receipt: PublicationReceipt) -> str:
        raise PublishingValidationError("S3-compatible delete is unsupported offline")


class RssPublicationAdapter:
    """Create canonical RSS bytes with one deterministic item per episode."""

    def __init__(self, object_store: ObjectStore | None = None) -> None:
        self.object_store = object_store
        self.feeds: dict[str, bytes] = {}
        self._items: dict[tuple[UUID, UUID, str], dict[str, bytes]] = {}
        self._references: dict[tuple[UUID, UUID, str], ObjectRef] = {}
        self._state_sequences: dict[tuple[UUID, UUID, str], int] = {}
        self._loaded_scopes: set[tuple[UUID, UUID, str]] = set()

    def feed(self, target: PublicationTarget) -> bytes:
        return self.feeds[f"{target.tenant_id}:{target.project_id}:{target.target_id}"]

    def rss_reference(self, target: PublicationTarget) -> ObjectRef | None:
        """Return the immutable object reference for the current canonical feed."""
        return self._references.get(
            (target.tenant_id, target.project_id, target.target_id)
        )

    def verify_receipt(
        self, target: PublicationTarget, receipt: PublicationReceipt
    ) -> None:
        """Re-read the receipt's immutable RSS object before an idempotent return."""
        payload = receipt.provenance.get("rss_object")
        if payload is None and self.object_store is None:
            return
        if not isinstance(payload, Mapping) or self.object_store is None:
            raise ObjectIntegrityError("RSS receipt object reference is unavailable")
        try:
            reference = ObjectRef(
                tenant_id=UUID(str(payload["tenant_id"])),
                project_id=UUID(str(payload["project_id"])),
                name=str(payload["name"]),
                media_type=str(payload["media_type"]),
                byte_count=payload["byte_count"],
                sha256=str(payload["sha256"]),
                storage_key=str(payload["storage_key"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ObjectIntegrityError(
                "RSS receipt object reference is invalid"
            ) from error
        if (
            reference.tenant_id != target.tenant_id
            or reference.project_id != target.project_id
        ):
            raise ObjectIntegrityError("RSS receipt object reference is out of scope")
        self.object_store.read(target.tenant_id, target.project_id, reference)

    def publish(
        self,
        package: EpisodePackage,
        target: PublicationTarget,
        artifacts: dict[str, bytes],
    ) -> str:
        title = package.episode_version_id
        root = ET.Element("rss", {"version": "2.0"})
        channel = ET.SubElement(root, "channel")
        ET.SubElement(channel, "title").text = target.show_id
        ET.SubElement(channel, "link").text = target.feed_url
        ET.SubElement(channel, "description").text = "PodDown publication"
        item = ET.Element("item")
        ET.SubElement(
            item, "guid", {"isPermaLink": "false"}
        ).text = package.episode_version_id
        ET.SubElement(item, "title").text = title
        audio = artifacts.get("episode.mp3", artifacts.get("episode.wav", b""))
        ET.SubElement(
            item,
            "enclosure",
            {
                "url": f"{target.feed_url}/{title}.mp3",
                "length": str(len(audio)),
                "type": "audio/mpeg",
            },
        )
        item_data = ET.tostring(item, encoding="utf-8")
        scope = (target.tenant_id, target.project_id, target.target_id)
        self._load_state(target, scope)
        items = self._items.get(scope, {})
        existing_item = items.get(package.episode_version_id)
        if existing_item is not None and existing_item != item_data:
            raise PublicationConflictError("RSS GUID content conflicts with history")
        staged_items = dict(items)
        staged_items[package.episode_version_id] = item_data
        for guid in sorted(staged_items):
            channel.append(ET.fromstring(staged_items[guid]))
        data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        if self.object_store is not None:
            reference = self.object_store.put(
                target.tenant_id,
                target.project_id,
                name="rss.xml",
                media_type="application/rss+xml",
                data=data,
            )
            sequence = self._state_sequences.get(scope, 0) + 1
            self.object_store.put(
                target.tenant_id,
                target.project_id,
                name=self._state_name(target, sequence),
                media_type="application/json",
                data=self._state_bytes(target, staged_items, sequence),
            )
            self._references[scope] = reference
            self._state_sequences[scope] = sequence
        self._items[scope] = staged_items
        self.feeds[f"{target.tenant_id}:{target.project_id}:{target.target_id}"] = data
        return f"rss:{target.target_id}:{package.episode_version_id}"

    def _load_state(
        self,
        target: PublicationTarget,
        scope: tuple[UUID, UUID, str],
    ) -> None:
        if scope in self._loaded_scopes:
            return
        if self.object_store is None:
            self._items.setdefault(scope, {})
            self._loaded_scopes.add(scope)
            return
        if not isinstance(self.object_store, ObjectStoreDiscovery):
            raise PublishingValidationError(
                "RSS object store does not support discovery"
            )
        highest: tuple[int, dict[str, bytes]] | None = None
        for reference in self.object_store.list(
            target.tenant_id,
            target.project_id,
            name_prefix=f"rss-state-{target.target_id}-",
        ):
            sequence, items = self._parse_state(
                target,
                reference.name,
                self.object_store.read(target.tenant_id, target.project_id, reference),
            )
            if highest is None or sequence > highest[0]:
                highest = (sequence, items)
        if highest is not None:
            self._state_sequences[scope], self._items[scope] = highest
        else:
            self._items.setdefault(scope, {})
        self._loaded_scopes.add(scope)

    @staticmethod
    def _state_name(target: PublicationTarget, sequence: int) -> str:
        return f"rss-state-{target.target_id}-{sequence:020d}"

    @classmethod
    def _state_bytes(
        cls,
        target: PublicationTarget,
        items: Mapping[str, bytes],
        sequence: int,
    ) -> bytes:
        return json.dumps(
            {
                "items": [
                    {"guid": guid, "xml": items[guid].decode("utf-8")}
                    for guid in sorted(items)
                ],
                "schema": "poddown.rss-state",
                "sequence": sequence,
                "target_id": target.target_id,
                "version": 1,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @classmethod
    def _parse_state(
        cls,
        target: PublicationTarget,
        name: str,
        data: bytes,
    ) -> tuple[int, dict[str, bytes]]:
        try:
            payload = json.loads(data)
            if not isinstance(payload, dict):
                raise ValueError("state is not an object")
            sequence = payload["sequence"]
            items = payload["items"]
            if (
                payload.get("schema") != "poddown.rss-state"
                or payload.get("version") != 1
                or payload.get("target_id") != target.target_id
                or type(sequence) is not int
                or sequence < 1
                or name != cls._state_name(target, sequence)
                or not isinstance(items, list)
            ):
                raise ValueError("state is invalid")
            parsed = {
                str(item["guid"]): str(item["xml"]).encode("utf-8") for item in items
            }
            if len(parsed) != len(items) or list(parsed) != sorted(parsed):
                raise ValueError("state items are invalid")
            for guid, item_xml in parsed.items():
                item = ET.fromstring(item_xml)
                if item.tag != "item" or item.findtext("guid") != guid:
                    raise ValueError("state item is invalid")
        except (
            ET.ParseError,
            KeyError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
        ) as error:
            raise PublishingValidationError("RSS state object is invalid") from error
        return sequence, parsed

    def update(self, receipt: PublicationReceipt) -> str:
        raise PublishingValidationError("RSS update is unsupported offline")

    def delete(self, receipt: PublicationReceipt) -> str:
        raise PublishingValidationError("RSS delete is unsupported offline")


class RecordedTransistorAdapter:
    """Offline Transistor contract adapter backed only by recorded fixture data."""

    def __init__(self, fixture: dict[str, object]) -> None:
        self.fixture = dict(fixture)

    def publish(
        self,
        package: EpisodePackage,
        target: PublicationTarget,
        artifacts: dict[str, bytes],
    ) -> str:
        external_id = self.fixture.get("id")
        if not isinstance(external_id, str) or not external_id:
            raise PublishingValidationError(
                "recorded Transistor fixture lacks episode id"
            )
        return external_id

    def update(self, receipt: PublicationReceipt) -> str:
        self._validate_mutation_fixture(receipt, "update", "updated")
        return f"transistor-updated:{receipt.external_id}"

    def delete(self, receipt: PublicationReceipt) -> str:
        self._validate_mutation_fixture(receipt, "delete", "deleted")
        return f"transistor-deleted:{receipt.external_id}"

    def _validate_mutation_fixture(
        self, receipt: PublicationReceipt, operation: str, status: str
    ) -> None:
        if (
            self.fixture.get("operation") != operation
            or self.fixture.get("status") != status
            or self.fixture.get("id") != receipt.external_id
        ):
            raise PublishingValidationError(
                "recorded Transistor mutation fixture does not match publication"
            )


SecretResolver = Callable[[str], str]


class TransistorPublicationAdapter:
    """Explicit Transistor API adapter with upload and draft-episode creation."""

    def __init__(
        self,
        base_url: str,
        *,
        secret_resolver: SecretResolver,
        transport: PublicationHttpTransport | None = None,
        timeout_seconds: float = 30.0,
        target_resolver: Callable[[str], PublicationTarget] | None = None,
        live_opt_in: bool = False,
    ) -> None:
        self.base_url = _publication_base_url(base_url)
        if not callable(secret_resolver):
            raise TypeError("secret_resolver must be callable")
        if type(live_opt_in) is not bool:
            raise TypeError("live_opt_in must be a boolean")
        if transport is None and not live_opt_in:
            raise PublishingAuthorizationError(
                "live Transistor publication requires explicit opt-in"
            )
        self._secret_resolver = secret_resolver
        self._transport = transport or UrllibPublicationTransport()
        self._timeout_seconds = timeout_seconds
        self._target_resolver = target_resolver
        self._targets_by_external_id: dict[str, PublicationTarget] = {}
        PublicationHttpRequest("GET", self.base_url, timeout_seconds=timeout_seconds)

    def publish(
        self,
        package: EpisodePackage,
        target: PublicationTarget,
        artifacts: dict[str, bytes],
    ) -> str:
        """Authorize an upload, upload exact bytes, then create a draft episode."""
        _require_transistor_target(target)
        audio_name, audio, media_type = _publishable_audio(package, artifacts)
        api_key = self._api_key(target)
        try:
            upload = self._request_json(
                PublicationHttpRequest(
                    "GET",
                    f"{self.base_url}/episodes/authorize_upload?filename={quote(audio_name)}",
                    headers={"x-api-key": api_key},
                    timeout_seconds=self._timeout_seconds,
                ),
                expected="upload authorization",
            )
            upload_attributes = _data_attributes(upload, "upload authorization")
            upload_url = _required_https_url(upload_attributes, "upload_url")
            audio_url = _required_https_url(upload_attributes, "audio_url")
            content_type = upload_attributes.get("content_type", media_type)
            if not isinstance(content_type, str) or not content_type.strip():
                raise PublishingValidationError("upload content type is missing")
            self._request_success(
                PublicationHttpRequest(
                    "PUT",
                    upload_url,
                    headers={"Content-Type": content_type},
                    body=audio,
                    timeout_seconds=self._timeout_seconds,
                ),
                expected="audio upload",
            )
            created = self._request_json(
                PublicationHttpRequest(
                    "POST",
                    f"{self.base_url}/episodes",
                    headers={"x-api-key": api_key},
                    form={
                        "episode[show_id]": target.show_id,
                        "episode[audio_url]": audio_url,
                        "episode[title]": package.episode_version_id,
                    },
                    timeout_seconds=self._timeout_seconds,
                ),
                expected="episode creation",
            )
            external_id = _data_id(created, "episode creation")
        except (PublicationTransportError, TimeoutError) as error:
            raise PublicationUncertainOutcomeError(
                "Transistor publication outcome is uncertain"
            ) from error
        self._targets_by_external_id[external_id] = target
        return external_id

    def update(self, receipt: PublicationReceipt) -> str:
        """Update a known draft title through a separately authorized service call."""
        target = self._target_for_receipt(receipt)
        api_key = self._api_key(target)
        try:
            payload = self._request_json(
                PublicationHttpRequest(
                    "PATCH",
                    f"{self.base_url}/episodes/{quote(receipt.external_id, safe='')}",
                    headers={"x-api-key": api_key},
                    form={"episode[title]": receipt.episode_version_id},
                    timeout_seconds=self._timeout_seconds,
                ),
                expected="episode update",
            )
        except (PublicationTransportError, TimeoutError) as error:
            raise PublicationUncertainOutcomeError(
                "Transistor update outcome is uncertain"
            ) from error
        return _data_id(payload, "episode update")

    def delete(self, receipt: PublicationReceipt) -> str:
        """Reject deletion because the documented Transistor API has no endpoint."""
        del receipt
        raise PublishingValidationError(
            "Transistor episode deletion is unsupported; unpublish explicitly instead"
        )

    def _api_key(self, target: PublicationTarget) -> str:
        try:
            value = self._secret_resolver(target.secret_ref)
        except Exception as error:
            raise PublishingAuthorizationError(
                "Transistor publication credential could not be resolved"
            ) from error
        if not isinstance(value, str) or not value.strip():
            raise PublishingAuthorizationError(
                "Transistor publication credential is missing"
            )
        return value.strip()

    def _target_for_receipt(self, receipt: PublicationReceipt) -> PublicationTarget:
        if not isinstance(receipt, PublicationReceipt):
            raise TypeError("receipt must be a PublicationReceipt")
        try:
            target = self._targets_by_external_id[receipt.external_id]
        except KeyError:
            if self._target_resolver is None:
                raise PublishingValidationError(
                    "Transistor publication target is unavailable for mutation"
                ) from None
            try:
                target = self._target_resolver(receipt.target_id)
            except Exception as error:
                raise PublishingValidationError(
                    "Transistor publication target is unavailable for mutation"
                ) from error
            if (
                not isinstance(target, PublicationTarget)
                or target.kind != "transistor"
                or target.target_id != receipt.target_id
                or target.tenant_id != receipt.tenant_id
                or target.project_id != receipt.project_id
            ):
                raise PublishingValidationError(
                    "Transistor publication target is inconsistent"
                ) from None
            self._targets_by_external_id[receipt.external_id] = target
        return target

    def _request_success(
        self, request: PublicationHttpRequest, *, expected: str
    ) -> PublicationHttpResponse:
        response = self._transport.request(request)
        if not isinstance(response, PublicationHttpResponse):
            raise PublishingValidationError(
                "publication transport returned invalid data"
            )
        if response.status in {408, 429, 500, 502, 503, 504}:
            raise PublicationTransportError(f"Transistor {expected} was unavailable")
        if not 200 <= response.status < 300:
            raise PublishingValidationError(
                f"Transistor {expected} failed with status {response.status}"
            )
        return response

    def _request_json(
        self, request: PublicationHttpRequest, *, expected: str
    ) -> dict[str, object]:
        response = self._request_success(request, expected=expected)
        try:
            value = json.loads(response.body)
        except (TypeError, json.JSONDecodeError) as error:
            raise PublishingValidationError(
                f"Transistor {expected} response is malformed"
            ) from error
        if not isinstance(value, dict):
            raise PublishingValidationError(
                f"Transistor {expected} response is malformed"
            )
        return value


def _publication_base_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PublishingValidationError("publication API base URL is required")
    normalized = value.strip().rstrip("/")
    try:
        parsed = urlsplit(normalized)
    except ValueError as error:
        raise PublishingValidationError(
            "publication API base URL is invalid"
        ) from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise PublishingValidationError("publication API base URL must be HTTPS")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _require_transistor_target(target: PublicationTarget) -> None:
    if not isinstance(target, PublicationTarget) or target.kind != "transistor":
        raise PublishingValidationError(
            "Transistor adapter requires a transistor target"
        )


def _publishable_audio(
    package: EpisodePackage, artifacts: dict[str, bytes]
) -> tuple[str, bytes, str]:
    if not isinstance(package, EpisodePackage) or not isinstance(artifacts, dict):
        raise PublishingValidationError("Transistor package inputs are invalid")
    for name in ("episode.mp3", "episode.wav"):
        reference = next((item for item in package.files if item.name == name), None)
        data = artifacts.get(name)
        if (
            reference is not None
            and isinstance(data, bytes)
            and len(data) == reference.byte_count
            and sha256(data).hexdigest() == reference.sha256
        ):
            return name, data, reference.media_type
    raise PublishingValidationError("Transistor package has no verified audio")


def _data_attributes(
    payload: Mapping[str, object], expected: str
) -> Mapping[str, object]:
    data = payload.get("data")
    attributes = data.get("attributes") if isinstance(data, Mapping) else None
    if not isinstance(attributes, Mapping):
        raise PublishingValidationError(f"Transistor {expected} response is malformed")
    return cast(Mapping[str, object], attributes)


def _required_https_url(attributes: Mapping[str, object], name: str) -> str:
    value = attributes.get(name)
    if not isinstance(value, str) or not value.strip():
        raise PublishingValidationError(f"Transistor {name} is missing")
    normalized = value.strip()
    try:
        parsed = urlsplit(normalized)
    except ValueError as error:
        raise PublishingValidationError(f"Transistor {name} is invalid") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise PublishingValidationError(f"Transistor {name} must be HTTPS")
    return normalized


def _data_id(payload: Mapping[str, object], expected: str) -> str:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise PublishingValidationError(f"Transistor {expected} response is malformed")
    value = data.get("id")
    if not isinstance(value, str) or not value.strip():
        raise PublishingValidationError(f"Transistor {expected} ID is missing")
    return value.strip()


class PublishingService:
    """Validate packages, execute one adapter, and retain replay-safe receipts."""

    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        adapters: dict[str, PublicationAdapter],
        publication_repository: PublicationReceiptRepository | None = None,
        receipt_store: PublicationReceiptStore | None = None,
    ) -> None:
        if publication_repository is not None and receipt_store is not None:
            raise TypeError("provide only one publication repository or receipt store")
        self.artifact_store = artifact_store
        self.adapters = dict(adapters)
        self.publication_repository = publication_repository
        self.receipt_store = receipt_store
        self._receipts: dict[tuple[UUID, UUID, str, str, str], PublicationReceipt] = {}
        self._attempts: dict[tuple[UUID, UUID, str, str, str], PublicationAttempt] = {}
        self._targets: dict[str, PublicationTarget] = {}
        self._mutations: dict[
            tuple[UUID, UUID, str, str], PublicationMutationReceipt
        ] = {}
        self._mutation_attempts: dict[tuple[UUID, UUID, str, str], str] = {}
        self._lock_guard = RLock()
        self._idempotency_locks: dict[tuple[object, ...], RLock] = {}

    def _idempotency_lock(self, key: tuple[object, ...]) -> RLock:
        with self._lock_guard:
            return self._idempotency_locks.setdefault(key, RLock())

    def attempt(
        self, idempotency_key: str, tenant_id: UUID, project_id: UUID
    ) -> PublicationAttempt:
        attempts = [
            attempt
            for key, attempt in self._attempts.items()
            if key[0] == tenant_id
            and key[1] == project_id
            and key[4] == idempotency_key
        ]
        if len(attempts) != 1:
            raise PublishingValidationError("publication attempt identity is ambiguous")
        return attempts[0]

    def publish(
        self,
        package: EpisodePackage,
        target: PublicationTarget,
        authorization: PublicationAuthorization,
        idempotency_key: str,
    ) -> PublicationReceipt:
        if authorization.operation != "publish":
            raise PublishingAuthorizationError(
                "explicit publish authorization is required"
            )
        if not idempotency_key.strip():
            raise PublishingValidationError("publication idempotency key is required")
        _validate_package(package)
        key = (
            target.tenant_id,
            target.project_id,
            package.episode_version_id,
            target.target_id,
            idempotency_key,
        )
        with self._idempotency_lock(key):
            attempt = self._attempts.setdefault(
                key,
                PublicationAttempt(
                    target.tenant_id, target.project_id, idempotency_key
                ),
            )
            existing = self._receipts.get(key)
            if existing is None and self.receipt_store is not None:
                existing = self.receipt_store.get_by_idempotency(
                    tenant_id=target.tenant_id,
                    project_id=target.project_id,
                    target_id=target.target_id,
                    idempotency_key=idempotency_key,
                )
                if existing is not None:
                    self._receipts[key] = existing
            package_identity = _package_identity(package)
            expected_target = _target_snapshot(target)
            durable_snapshot = _durable_publication_snapshot(target, package_identity)
            repository = self.publication_repository
            lookup = getattr(repository, "lookup", None) if repository else None
            if callable(lookup):
                durable_existing = lookup(
                    tenant_id=target.tenant_id,
                    project_id=target.project_id,
                    episode_version_id=package.episode_version_id,
                    target_id=target.target_id,
                    idempotency_key=idempotency_key,
                )
                if durable_existing is not None:
                    if durable_existing.status in {"pending", "uncertain"}:
                        from poddown.publication_repository import (
                            PublicationOutcomeUncertain,
                        )

                        raise PublicationOutcomeUncertain(
                            "publication outcome is unknown; provider retry is unsafe"
                        )
                    if durable_existing.status == "succeeded":
                        receipt = durable_existing.receipt
                        if (
                            not isinstance(receipt, PublicationReceipt)
                            or durable_existing.request.target_snapshot
                            != durable_snapshot
                        ):
                            raise PublicationConflictError(
                                "idempotency key is bound to another publication"
                            )
                        self._verify_replayed_rss_receipt(target, receipt)
                        self._receipts[key] = receipt
                        attempt.state = "completed"
                        attempt.retryable = False
                        attempt.receipt = receipt
                        self._targets[receipt.publication_id] = target
                        return receipt
            if attempt.state == "unknown" or (
                attempt.state == "failed" and not attempt.retryable
            ):
                raise PublicationConflictError(
                    "publication outcome is unknown; retry is unsafe"
                )
            if existing is not None:
                if (
                    existing.episode_version_id != package.episode_version_id
                    or existing.provenance.get("package_identity") != package_identity
                    or existing.provenance.get("target") != expected_target
                ):
                    raise PublicationConflictError(
                        "idempotency key is bound to another publication"
                    )
                self._verify_replayed_rss_receipt(target, existing)
                return existing
            adapter = self.adapters.get(target.kind)
            if adapter is None:
                raise PublishingValidationError("publication adapter is unavailable")
            try:
                artifacts = _read_artifacts(self.artifact_store, package)
            except Exception as error:
                durable_failure: DurablePublicationAttempt | None = None
                if repository is not None:
                    try:
                        recovery_artifacts = _read_artifacts(
                            self.artifact_store, package
                        )
                        recovery_sha256 = package_sha256_for_artifacts(
                            package, recovery_artifacts
                        )
                        from poddown.publication_repository import (
                            PublicationAttemptRequest,
                        )

                        durable_failure = repository.begin(
                            PublicationAttemptRequest(
                                tenant_id=target.tenant_id,
                                project_id=target.project_id,
                                episode_version_id=package.episode_version_id,
                                target_id=target.target_id,
                                idempotency_key=idempotency_key,
                                package_sha256=recovery_sha256,
                                target_snapshot=durable_snapshot,
                            )
                        )
                    except Exception:
                        durable_failure = None
                _record_known_failure(attempt, repository, durable_failure, error)
                raise
            package_sha256 = package_sha256_for_artifacts(package, artifacts)
            durable_attempt: DurablePublicationAttempt | None = None
            if repository is not None:
                from poddown.publication_repository import PublicationAttemptRequest

                try:
                    durable_attempt = repository.begin(
                        PublicationAttemptRequest(
                            tenant_id=target.tenant_id,
                            project_id=target.project_id,
                            episode_version_id=package.episode_version_id,
                            target_id=target.target_id,
                            idempotency_key=idempotency_key,
                            package_sha256=package_sha256,
                            target_snapshot=durable_snapshot,
                        )
                    )
                except Exception as error:
                    from poddown.publication_repository import (
                        PublicationIdentityConflict,
                    )

                    if isinstance(error, PublicationIdentityConflict):
                        raise PublicationConflictError(str(error)) from error
                    raise
                if durable_attempt.receipt is not None:
                    receipt = durable_attempt.receipt
                    self._verify_replayed_rss_receipt(target, receipt)
                    self._receipts[key] = receipt
                    attempt.state = "completed"
                    attempt.retryable = False
                    attempt.receipt = receipt
                    self._targets[receipt.publication_id] = target
                    return receipt
            try:
                external_id = adapter.publish(package, target, artifacts)
            except Exception as error:
                if not isinstance(getattr(error, "outcome", None), str):
                    _record_known_failure(attempt, repository, durable_attempt, error)
                    raise error
                outcome = _adapter_outcome(error)
                if outcome == PublicationOutcome.UNCERTAIN:
                    safe_error = sanitize_publication_error(error)
                    attempt.state = "unknown"
                    attempt.retryable = False
                    attempt.failure = safe_error
                    if durable_attempt is not None and repository is not None:
                        repository.mark_uncertain(
                            durable_attempt,
                            safe_error,
                            external_reference=getattr(
                                error, "external_reference", None
                            ),
                        )
                elif outcome in {
                    PublicationOutcome.KNOWN_NOT_APPLIED,
                    PublicationOutcome.COMPENSATED,
                }:
                    safe_error = _record_known_failure(
                        attempt, repository, durable_attempt, error
                    )
                else:
                    _record_known_failure(attempt, repository, durable_attempt, error)
                    raise error
                if isinstance(error, PublicationUncertainOutcomeError):
                    raise error
                raise PublicationAdapterError(
                    safe_error,
                    outcome=outcome,
                    external_reference=getattr(error, "external_reference", None),
                    status_code=getattr(error, "status_code", None),
                ) from error
            rss_reference = (
                adapter.rss_reference(target)
                if isinstance(adapter, RssPublicationAdapter)
                else None
            )
            receipt = PublicationReceipt(
                publication_id=str(uuid7()),
                tenant_id=target.tenant_id,
                project_id=target.project_id,
                episode_version_id=package.episode_version_id,
                target_id=target.target_id,
                idempotency_key=idempotency_key,
                package_sha256=package_sha256,
                external_id=external_id,
                status=(
                    "resumed"
                    if attempt.state == "failed"
                    or (
                        durable_attempt is not None
                        and durable_attempt.attempt_number > 1
                    )
                    else "published"
                ),
                authorization=authorization,
                disclosure=target.disclosure,
                provenance={
                    "package_identity": package_identity,
                    "episode_version_id": package.episode_version_id,
                    "adapter": target.kind,
                    "authorization_decision_id": authorization.decision_id,
                    "target": expected_target,
                    **(
                        {"rss_object": rss_reference.to_dict()}
                        if rss_reference is not None
                        else {}
                    ),
                },
            )
            if durable_attempt is not None and repository is not None:
                try:
                    repository.succeed(durable_attempt, receipt)
                except Exception as error:
                    repository.mark_uncertain(
                        durable_attempt,
                        str(error),
                        external_reference=external_id,
                    )
                    raise
            elif self.receipt_store is not None:
                receipt = self.receipt_store.save(receipt)
            self._receipts[key] = receipt
            attempt.state = "completed"
            attempt.retryable = False
            attempt.receipt = receipt
            self._targets[receipt.publication_id] = target
            return receipt

    def _verify_replayed_rss_receipt(
        self,
        target: PublicationTarget,
        receipt: PublicationReceipt,
    ) -> None:
        if target.kind != "rss":
            return
        adapter = self.adapters.get(target.kind)
        if not isinstance(adapter, RssPublicationAdapter):
            raise PublishingValidationError("RSS publication adapter is unavailable")
        adapter.verify_receipt(target, receipt)

    def update(
        self,
        receipt: PublicationReceipt,
        authorization: PublicationAuthorization,
        idempotency_key: str,
    ) -> PublicationMutationReceipt:
        if authorization.operation != "update":
            raise PublishingAuthorizationError(
                "separate update authorization is required"
            )
        return self._mutate(receipt, authorization, idempotency_key, "update")

    def delete(
        self,
        receipt: PublicationReceipt,
        authorization: PublicationAuthorization,
        idempotency_key: str,
    ) -> PublicationMutationReceipt:
        if authorization.operation != "delete":
            raise PublishingAuthorizationError(
                "separate delete authorization is required"
            )
        return self._mutate(receipt, authorization, idempotency_key, "delete")

    def _mutate(
        self,
        receipt: PublicationReceipt,
        authorization: PublicationAuthorization,
        idempotency_key: str,
        operation: str,
    ) -> PublicationMutationReceipt:
        key = (receipt.tenant_id, receipt.project_id, operation, idempotency_key)
        lock_key = (receipt.tenant_id, receipt.project_id, idempotency_key)
        with self._idempotency_lock(lock_key):
            target = self._targets.get(receipt.publication_id)
            if target is None:
                raise PublishingValidationError("publication provenance is unavailable")
            existing = self._mutations.get(key)
            if existing is not None:
                if (
                    existing.publication_id != receipt.publication_id
                    or existing.target_id != receipt.target_id
                    or existing.provenance.get("target")
                    != receipt.provenance.get("target")
                ):
                    raise PublicationConflictError(
                        "mutation idempotency key is bound to another publication"
                    )
                return existing
            for prior_key in self._mutation_attempts:
                if prior_key[:2] == key[:2] and prior_key[3] == idempotency_key:
                    if prior_key == key:
                        raise PublicationConflictError(
                            "mutation outcome is unknown; retry is unsafe"
                        )
                    raise PublicationConflictError(
                        "mutation idempotency key is bound to another operation"
                    )
            adapter = self.adapters[target.kind]
            self._mutation_attempts[key] = "in-flight"
            try:
                external_id = getattr(adapter, operation)(receipt)
                mutation = PublicationMutationReceipt(
                    publication_id=receipt.publication_id,
                    tenant_id=receipt.tenant_id,
                    project_id=receipt.project_id,
                    target_id=receipt.target_id,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    status=f"{operation}d",
                    authorization=authorization,
                    provenance={
                        "target": receipt.provenance["target"],
                        "external_id": external_id,
                    },
                )
                self._mutations[key] = mutation
            except Exception:
                self._mutation_attempts[key] = "unknown"
                raise
            self._mutation_attempts[key] = "completed"
            return mutation


__all__ = [
    "DisclosurePolicy",
    "FilesystemPublicationAdapter",
    "PublicationAuthorization",
    "PublicationConflictError",
    "PublicationActivityRequest",
    "PublicationAdapter",
    "PublicationReceipt",
    "PublicationReceiptStore",
    "PublicationMutationReceipt",
    "PublicationHttpRequest",
    "PublicationHttpResponse",
    "PublicationHttpTransport",
    "PublicationAttempt",
    "PublicationAdapterError",
    "PublicationOutcome",
    "PublicationReceiptRepository",
    "PublicationTransportError",
    "PublicationUncertainOutcomeError",
    "PublicationTarget",
    "Publisher",
    "PublishingAuthorizationError",
    "PublishingError",
    "PublishingService",
    "PublishingValidationError",
    "RecordedTransistorAdapter",
    "RssPublicationAdapter",
    "S3CompatiblePublicationAdapter",
    "SecretResolver",
    "TransistorPublicationAdapter",
    "sanitize_publication_error",
]
