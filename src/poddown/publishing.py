"""Offline-safe immutable publication contracts and replaceable adapters."""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Protocol
from uuid import UUID, uuid4

from poddown.artifacts import ArtifactStore
from poddown.object_storage import (
    ObjectIntegrityError,
    ObjectNotFound,
    ObjectRef,
    ObjectStore,
    storage_key_for,
)
from poddown.packages import EpisodePackage


class PublishingError(ValueError):
    """Base publication contract error."""


class PublishingValidationError(PublishingError):
    """Publication input or package evidence is invalid."""


class PublishingAuthorizationError(PublishingError):
    """An operation lacks its explicit authorization decision."""


class PublicationConflictError(PublishingError, RuntimeError):
    """An idempotency key or immutable publication conflicts with prior evidence."""


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
            "provenance": dict(self.provenance),
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
            "provenance": dict(self.provenance),
        }


class PublicationAdapter(Protocol):
    """Provider-neutral adapter boundary."""

    def publish(
        self,
        package: EpisodePackage,
        target: PublicationTarget,
        artifacts: dict[str, bytes],
    ) -> str:
        """Publish exact package bytes and return an external identifier."""


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


def _package_identity(package: EpisodePackage) -> str:
    return sha256(
        json.dumps(package.to_dict(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _read_artifacts(store: ArtifactStore, package: EpisodePackage) -> dict[str, bytes]:
    if package.provenance.qa != "pass":
        raise PublishingValidationError("only QA-passed packages can publish")
    if package.provenance.critical_token_accuracy != 1.0:
        raise PublishingValidationError("only fully verified packages can publish")
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
    def _destination(root: Path, target: PublicationTarget) -> Path:
        return (
            root
            / "tenants"
            / str(target.tenant_id)
            / "projects"
            / str(target.project_id)
            / target.target_id
        )

    def publish(
        self,
        package: EpisodePackage,
        target: PublicationTarget,
        artifacts: dict[str, bytes],
    ) -> str:
        destination = self._destination(self.root, target)
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
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            with contextlib.suppress(OSError):
                destination.parent.rmdir()
            with contextlib.suppress(OSError):
                destination.parent.parent.rmdir()
            with contextlib.suppress(OSError):
                destination.parent.parent.parent.rmdir()
            with contextlib.suppress(OSError):
                destination.parent.parent.parent.parent.rmdir()
            raise
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
            new_refs: dict[str, ObjectRef] = {}
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
                        new_refs[name] = reference
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
                cleanup_error: Exception | None = None
                for reference in new_refs.values():
                    try:
                        self.object_store.delete(
                            target.tenant_id, target.project_id, reference
                        )
                    except Exception as cleanup:
                        cleanup_error = cleanup
                self.staged_references.pop(attempt_key, None)
                self.final_references.pop(target.target_id, None)
                self.completed.discard(target.target_id)
                for name in artifacts:
                    self.references.pop((target.target_id, name), None)
                if cleanup_error is not None:
                    raise PublishingValidationError(
                        "object publication cleanup failed"
                    ) from cleanup_error
                raise error
            self.staged_references.pop(attempt_key, None)
        return f"s3:{target.target_id}:{package.episode_version_id}"

    def update(self, receipt: PublicationReceipt) -> str:
        raise PublishingValidationError("S3-compatible update is unsupported offline")

    def delete(self, receipt: PublicationReceipt) -> str:
        raise PublishingValidationError("S3-compatible delete is unsupported offline")


class RssPublicationAdapter:
    """Create canonical RSS bytes with one deterministic item per episode."""

    def __init__(self) -> None:
        self.feeds: dict[str, bytes] = {}
        self._items: dict[tuple[UUID, UUID, str], dict[str, bytes]] = {}

    def feed(self, target: PublicationTarget) -> bytes:
        return self.feeds[f"{target.tenant_id}:{target.project_id}:{target.target_id}"]

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
        items = self._items.setdefault(scope, {})
        existing_item = items.get(package.episode_version_id)
        if existing_item is not None and existing_item != item_data:
            raise PublicationConflictError("RSS GUID content conflicts with history")
        items[package.episode_version_id] = item_data
        for guid in sorted(items):
            channel.append(ET.fromstring(items[guid]))
        data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        self.feeds[f"{target.tenant_id}:{target.project_id}:{target.target_id}"] = data
        return f"rss:{target.target_id}:{package.episode_version_id}"

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


class PublishingService:
    """Validate packages, execute one adapter, and retain replay-safe receipts."""

    def __init__(
        self, *, artifact_store: ArtifactStore, adapters: dict[str, PublicationAdapter]
    ) -> None:
        self.artifact_store = artifact_store
        self.adapters = dict(adapters)
        self._receipts: dict[tuple[UUID, UUID, str], PublicationReceipt] = {}
        self._attempts: dict[tuple[UUID, UUID, str], PublicationAttempt] = {}
        self._targets: dict[str, PublicationTarget] = {}
        self._mutations: dict[
            tuple[UUID, UUID, str, str], PublicationMutationReceipt
        ] = {}

    def attempt(
        self, idempotency_key: str, tenant_id: UUID, project_id: UUID
    ) -> PublicationAttempt:
        return self._attempts[(tenant_id, project_id, idempotency_key)]

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
        key = (target.tenant_id, target.project_id, idempotency_key)
        attempt = self._attempts.setdefault(
            key,
            PublicationAttempt(target.tenant_id, target.project_id, idempotency_key),
        )
        existing = self._receipts.get(key)
        if existing is not None:
            if (
                existing.target_id != target.target_id
                or existing.package_sha256 != package.provenance.final_sha256
            ):
                raise PublicationConflictError(
                    "idempotency key is bound to another publication"
                )
            return existing
        artifacts = _read_artifacts(self.artifact_store, package)
        adapter = self.adapters.get(target.kind)
        if adapter is None:
            raise PublishingValidationError("publication adapter is unavailable")
        try:
            external_id = adapter.publish(package, target, artifacts)
        except Exception as error:
            attempt.state = "failed"
            attempt.retryable = True
            attempt.failure = str(error)
            raise
        receipt = PublicationReceipt(
            publication_id=str(uuid4()),
            tenant_id=target.tenant_id,
            project_id=target.project_id,
            episode_version_id=package.episode_version_id,
            target_id=target.target_id,
            idempotency_key=idempotency_key,
            package_sha256=package.provenance.final_sha256,
            external_id=external_id,
            status="resumed" if attempt.state == "failed" else "published",
            authorization=authorization,
            disclosure=target.disclosure,
            provenance={
                "package_identity": _package_identity(package),
                "adapter": target.kind,
                "authorization_decision_id": authorization.decision_id,
                "target": {
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
                    "package_sha256": package.provenance.final_sha256,
                    "package_identity": _package_identity(package),
                },
            },
        )
        self._receipts[key] = receipt
        attempt.state = "completed"
        attempt.retryable = False
        attempt.receipt = receipt
        self._targets[receipt.publication_id] = target
        return receipt

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
        target = self._targets.get(receipt.publication_id)
        if target is None:
            raise PublishingValidationError("publication provenance is unavailable")
        key = (receipt.tenant_id, receipt.project_id, operation, idempotency_key)
        existing = self._mutations.get(key)
        if existing is not None:
            if (
                existing.publication_id != receipt.publication_id
                or existing.target_id != receipt.target_id
                or existing.provenance
                != {
                    "target": receipt.provenance["target"],
                    "external_id": receipt.external_id,
                }
            ):
                raise PublicationConflictError(
                    "mutation idempotency key is bound to another publication"
                )
            return existing
        for prior_key, _prior in self._mutations.items():
            if prior_key[:2] == key[:2] and prior_key[3] == idempotency_key:
                raise PublicationConflictError(
                    "mutation idempotency key is bound to another operation"
                )
        adapter = self.adapters[target.kind]
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
        return mutation


__all__ = [
    "DisclosurePolicy",
    "FilesystemPublicationAdapter",
    "PublicationAuthorization",
    "PublicationConflictError",
    "PublicationReceipt",
    "PublicationMutationReceipt",
    "PublicationAttempt",
    "PublicationTarget",
    "Publisher",
    "PublishingAuthorizationError",
    "PublishingError",
    "PublishingService",
    "PublishingValidationError",
    "RecordedTransistorAdapter",
    "RssPublicationAdapter",
    "S3CompatiblePublicationAdapter",
]
