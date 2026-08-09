"""Filesystem persistence for immutable audio artifacts and render records."""

import hashlib
import json
import os
import re
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import NamedTemporaryFile

from poddown.audio.contracts import (
    ArtifactRef,
    ProviderCostEvent,
    RenderCandidate,
    RenderOutcome,
)
from poddown.domain import ProviderUsage

_IDEMPOTENCY_KEY = re.compile(r"^render-[0-9a-f]{64}$")


class ArtifactIntegrityError(RuntimeError):
    """Raised when persisted artifact or render evidence cannot be trusted."""


class IdempotencyConflictError(RuntimeError):
    """Raised when one idempotency key is associated with different evidence."""


class FilesystemArtifactStore:
    """Persist content-addressed audio bytes without replacing existing objects."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def put(self, content: bytes, *, media_type: str) -> ArtifactRef:
        """Store bytes once and return their immutable content-addressed reference."""
        digest = hashlib.sha256(content).hexdigest()
        relative_path = Path("artifacts") / digest[:2] / f"{digest}.wav"
        artifact = ArtifactRef(digest, media_type, len(content), str(relative_path))
        path = self._path_for(artifact)
        path.parent.mkdir(parents=True, exist_ok=True)

        with NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        try:
            try:
                os.link(temporary_path, path)
            except FileExistsError:
                self._verify(path, artifact)
            else:
                self._verify(path, artifact)
        finally:
            temporary_path.unlink(missing_ok=True)
        return artifact

    def read(self, artifact: ArtifactRef) -> bytes:
        """Return verified bytes for an immutable artifact reference."""
        path = self._path_for(artifact)
        self._verify(path, artifact)
        try:
            return path.read_bytes()
        except OSError as error:
            raise ArtifactIntegrityError("artifact cannot be read") from error

    def _path_for(self, artifact: ArtifactRef) -> Path:
        relative_path = Path(artifact.relative_path)
        expected_path = (
            Path("artifacts") / artifact.sha256[:2] / f"{artifact.sha256}.wav"
        )
        if relative_path != expected_path:
            raise ArtifactIntegrityError("artifact path is not canonical")
        if relative_path.is_absolute():
            raise ArtifactIntegrityError("artifact path must be relative")
        path = (self._root / relative_path).resolve()
        try:
            path.relative_to(self._root)
        except ValueError as error:
            raise ArtifactIntegrityError(
                "artifact path escapes storage root"
            ) from error
        return path

    @staticmethod
    def _verify(path: Path, artifact: ArtifactRef) -> None:
        try:
            content = path.read_bytes()
        except OSError as error:
            raise ArtifactIntegrityError("artifact object is missing") from error
        if len(content) != artifact.size_bytes:
            raise ArtifactIntegrityError("artifact size does not match its reference")
        if hashlib.sha256(content).hexdigest() != artifact.sha256:
            raise ArtifactIntegrityError("artifact digest does not match its reference")


class FilesystemRenderRecordStore:
    """Persist immutable render outcomes keyed by idempotency key."""

    def __init__(
        self, root: Path, artifact_store: FilesystemArtifactStore | None = None
    ) -> None:
        self._root = root.resolve()
        self._artifacts = artifact_store or FilesystemArtifactStore(
            self._root.parent / "artifacts"
        )

    def save(self, outcome: RenderOutcome) -> None:
        """Store an outcome once or reject conflicting evidence for its key."""
        self._validate_durable_outcome(outcome, outcome.candidate.idempotency_key)
        path = self._path_for(outcome.candidate.idempotency_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            self._serialize(outcome), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

        with NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        try:
            try:
                os.link(temporary_path, path)
            except FileExistsError:
                if self.load(outcome.candidate.idempotency_key) != outcome:
                    raise IdempotencyConflictError(
                        "idempotency key already has different render evidence"
                    ) from None
        finally:
            temporary_path.unlink(missing_ok=True)

    def find(self, idempotency_key: str) -> RenderOutcome | None:
        """Return an existing outcome, or None when this is a first render."""
        path = self._path_for(idempotency_key)
        if not path.exists():
            return None
        return self.load(idempotency_key)

    def load(self, idempotency_key: str) -> RenderOutcome:
        """Load a complete outcome or fail closed when persisted evidence is invalid."""
        path = self._path_for(idempotency_key)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as error:
            raise ArtifactIntegrityError("render record is missing") from error
        try:
            payload = json.loads(raw)
            outcome = self._deserialize(payload)
            self._validate_durable_outcome(outcome, idempotency_key)
            self._artifacts.read(outcome.candidate.artifact)
            return outcome
        except (
            InvalidOperation,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            raise ArtifactIntegrityError("render record is malformed") from error

    @staticmethod
    def _validate_durable_outcome(outcome: RenderOutcome, lookup_key: str) -> None:
        candidate = outcome.candidate
        if candidate.idempotency_key != lookup_key:
            raise ArtifactIntegrityError(
                "render record candidate key does not match lookup key"
            )
        if outcome.replayed or outcome.cost_event is None:
            raise ArtifactIntegrityError(
                "render record must contain new-render cost evidence"
            )
        cost_event = outcome.cost_event
        if (
            cost_event.event_id != f"cost-{candidate.candidate_id}"
            or cost_event.candidate_id != candidate.candidate_id
            or cost_event.provider != candidate.provider
            or cost_event.usage != candidate.usage
            or cost_event.cost != candidate.cost
        ):
            raise ArtifactIntegrityError("render record cost evidence is inconsistent")

    def _path_for(self, idempotency_key: str) -> Path:
        if not isinstance(idempotency_key, str) or not _IDEMPOTENCY_KEY.fullmatch(
            idempotency_key
        ):
            raise ArtifactIntegrityError("idempotency key is not canonical")
        relative_path = Path("records") / f"{idempotency_key}.json"
        path = (self._root / relative_path).resolve()
        try:
            path.relative_to(self._root)
        except ValueError as error:
            raise ArtifactIntegrityError("record path escapes storage root") from error
        return path

    @staticmethod
    def _serialize(outcome: RenderOutcome) -> dict[str, object]:
        candidate = asdict(outcome.candidate)
        candidate["cost"] = str(outcome.candidate.cost)
        candidate["usage"] = asdict(outcome.candidate.usage)
        artifact = candidate["artifact"]
        assert isinstance(artifact, dict)
        cost_event = outcome.cost_event
        return {
            "candidate": candidate,
            "cost_event": (
                None
                if cost_event is None
                else {
                    "event_id": cost_event.event_id,
                    "candidate_id": cost_event.candidate_id,
                    "provider": cost_event.provider,
                    "usage": asdict(cost_event.usage),
                    "cost": str(cost_event.cost),
                }
            ),
            "replayed": outcome.replayed,
        }

    @staticmethod
    def _deserialize(payload: object) -> RenderOutcome:
        if not isinstance(payload, dict):
            raise ValueError("record must be an object")
        candidate_payload = payload["candidate"]
        if not isinstance(candidate_payload, dict):
            raise ValueError("candidate must be an object")
        artifact_payload = candidate_payload["artifact"]
        usage_payload = candidate_payload["usage"]
        if not isinstance(artifact_payload, dict) or not isinstance(
            usage_payload, dict
        ):
            raise ValueError("candidate metadata must be objects")
        artifact = ArtifactRef(
            sha256=artifact_payload["sha256"],
            media_type=artifact_payload["media_type"],
            size_bytes=artifact_payload["size_bytes"],
            relative_path=artifact_payload["relative_path"],
        )
        usage = ProviderUsage(
            input_units=usage_payload["input_units"],
            output_units=usage_payload["output_units"],
        )
        candidate = RenderCandidate(
            candidate_id=candidate_payload["candidate_id"],
            idempotency_key=candidate_payload["idempotency_key"],
            segment_id=candidate_payload["segment_id"],
            speaker_id=candidate_payload["speaker_id"],
            attempt=candidate_payload["attempt"],
            take_index=candidate_payload["take_index"],
            voice_asset_id=candidate_payload["voice_asset_id"],
            expected_spoken_text=candidate_payload["expected_spoken_text"],
            provider=candidate_payload["provider"],
            model=candidate_payload["model"],
            request_id=candidate_payload["request_id"],
            usage=usage,
            cost=Decimal(candidate_payload["cost"]),
            artifact=artifact,
        )
        cost_event_payload = payload["cost_event"]
        cost_event: ProviderCostEvent | None = None
        if cost_event_payload is not None:
            if not isinstance(cost_event_payload, dict):
                raise ValueError("cost event must be an object")
            event_usage_payload = cost_event_payload["usage"]
            if not isinstance(event_usage_payload, dict):
                raise ValueError("cost event usage must be an object")
            cost_event = ProviderCostEvent(
                event_id=cost_event_payload["event_id"],
                candidate_id=cost_event_payload["candidate_id"],
                provider=cost_event_payload["provider"],
                usage=ProviderUsage(
                    input_units=event_usage_payload["input_units"],
                    output_units=event_usage_payload["output_units"],
                ),
                cost=Decimal(cost_event_payload["cost"]),
            )
        return RenderOutcome(
            candidate=candidate,
            cost_event=cost_event,
            replayed=payload["replayed"],
        )
