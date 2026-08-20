"""Provider-neutral Temporal activities for durable audio rendering."""

from __future__ import annotations

import asyncio
import fcntl
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from inspect import isawaitable
from pathlib import Path
from threading import Lock
from time import perf_counter_ns
from typing import Any
from uuid import UUID

from temporalio import activity
from temporalio.exceptions import ApplicationError

from poddown.audio.contracts import AudioRenderer, RenderRequest
from poddown.audio.diagnostics import (
    AudioDiagnostics,
    AudioDiagnosticsError,
    diagnose_wav,
)
from poddown.audio.render import DurableRenderService, RenderRejectedError
from poddown.audio.rights import RightsDeniedError, VoiceConsent
from poddown.audio.selection import CandidateQuality, CandidateSelectionError
from poddown.audio.storage import (
    ArtifactIntegrityError,
    FilesystemArtifactStore,
    FilesystemQualityRecordStore,
    FilesystemTranscriptionRecordStore,
    IdempotencyConflictError,
)
from poddown.audio.workflow import (
    PUBLISH_EPISODE_ACTIVITY_NAME,
    RENDER_SEGMENT_ACTIVITY_NAME,
    EpisodeWorkflowInput,
    MalformedAudioError,
    RightsFailureError,
    SegmentWorkflowInput,
    TranscriptionFailureError,
    TranscriptionTransientError,
    WorkflowContractError,
    activity_key_for,
)
from poddown.domain import ProviderUsage
from poddown.packages import EpisodePackage, manifest_sha256_for
from poddown.provider_routes import ProviderRoute
from poddown.providers.contracts import (
    ProviderEvidence,
    Transcriber,
    TranscriptResult,
    provider_usage_to_mapping,
)
from poddown.providers.http import ProviderRateLimited, ProviderRequestFailed
from poddown.providers.policy import (
    ProviderDispatchDecision,
    ProviderDispatchPreflight,
    ProviderDispatchRequest,
)
from poddown.publishing import (
    PublicationActivityRequest,
    PublicationAuthorization,
    PublicationConflictError,
    PublicationTarget,
    PublicationUncertainOutcomeError,
    PublishingService,
    PublishingValidationError,
)
from poddown.qa.fidelity import evaluate_critical_tokens

type QualityEvaluator = Callable[
    [RenderRequest, bytes, AudioDiagnostics, tuple[str, ...]],
    CandidateQuality | Awaitable[CandidateQuality],
]
type ActivityHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
type ProviderEvidenceRecorder = Callable[[ProviderEvidence], object]
type ProviderEvidenceRecorderFactory = Callable[
    [EpisodeWorkflowInput], ProviderEvidenceRecorder
]
type ProviderEvidenceClock = Callable[[], datetime]
type PublicationRequestResolver = Callable[[dict[str, Any]], PublicationActivityRequest]
type PublicationPackageResolver = Callable[[str], EpisodePackage | None]
type PublicationPackageDigestResolver = Callable[[EpisodePackage], str]
type PublicationTargetResolver = Callable[[UUID, UUID, str], PublicationTarget | None]


class ProviderDispatchRejectedError(WorkflowContractError):
    """Raised when provider preflight denies an operation before dispatch."""

    def __init__(self, decision: ProviderDispatchDecision) -> None:
        self.decision = decision
        super().__init__(
            "provider dispatch denied: "
            + ",".join(reason.value for reason in decision.reasons)
        )


@dataclass(frozen=True, slots=True)
class ProviderActivityDispatchPolicy:
    """Secret-free provider preflight metadata for one durable activity."""

    preflight: ProviderDispatchPreflight
    route: ProviderRoute
    render_estimated_cost: Decimal
    transcription_estimated_cost: Decimal
    episode_cost: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        """Reject incomplete activity policy metadata before registration."""
        if not isinstance(self.preflight, ProviderDispatchPreflight):
            raise TypeError("preflight must be a ProviderDispatchPreflight")
        if not isinstance(self.route, ProviderRoute):
            raise TypeError("route must be a ProviderRoute")
        for name in (
            "render_estimated_cost",
            "transcription_estimated_cost",
            "episode_cost",
        ):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be a finite non-negative Decimal")

    def require_render(
        self, request: RenderRequest, consent: VoiceConsent | None
    ) -> None:
        """Require exact route eligibility before renderer dispatch."""
        self._require(
            ProviderDispatchRequest(
                episode_id=request.episode_id,
                route_id=self.route.route_id,
                operation="render",
                provider=request.provider,
                model=request.model,
                voice_asset_id=request.voice_asset_id,
                required_capabilities=self.route.renderer.required_capabilities,
                estimated_cost=self.render_estimated_cost,
                episode_cost=self.episode_cost,
            ),
            consent,
        )

    def require_transcription(
        self, request: RenderRequest, consent: VoiceConsent | None
    ) -> None:
        """Require exact route eligibility before transcriber dispatch."""
        binding = self.route.transcriber
        self._require(
            ProviderDispatchRequest(
                episode_id=request.episode_id,
                route_id=self.route.route_id,
                operation="transcribe",
                provider=binding.provider,
                model=binding.model,
                voice_asset_id=binding.voice_asset_id,
                required_capabilities=binding.required_capabilities,
                estimated_cost=self.transcription_estimated_cost,
                episode_cost=self.episode_cost + self.render_estimated_cost,
            ),
            consent,
        )

    def _require(
        self, request: ProviderDispatchRequest, consent: VoiceConsent | None
    ) -> None:
        """Map a denied decision to one stable, structured activity error."""
        decision = self.preflight.evaluate(request, consent)
        if not decision.allowed:
            raise ProviderDispatchRejectedError(decision)


_TRANSCRIPTION_CLAIMS: dict[tuple[Path, str], Lock] = {}
_TRANSCRIPTION_CLAIMS_GUARD = Lock()


@asynccontextmanager
async def _transcription_claim(
    records: FilesystemTranscriptionRecordStore, candidate_id: str
) -> AsyncIterator[None]:
    """Hold a process and filesystem claim until transcription evidence is saved."""
    root = records._root
    claim_key = (root, candidate_id)
    with _TRANSCRIPTION_CLAIMS_GUARD:
        process_lock = _TRANSCRIPTION_CLAIMS.setdefault(claim_key, Lock())
    await asyncio.to_thread(process_lock.acquire)
    claim_path = root / ".claims" / f"{candidate_id}.lock"
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    claim_file = claim_path.open("a", encoding="utf-8")
    try:
        await asyncio.to_thread(fcntl.flock, claim_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            await asyncio.to_thread(fcntl.flock, claim_file.fileno(), fcntl.LOCK_UN)
    finally:
        claim_file.close()
        process_lock.release()


def _quality_cache_key(request: RenderRequest, critical_tokens: tuple[str, ...]) -> str:
    """Bind cached quality evidence to the ordered QA token inputs."""
    payload = json.dumps(
        {"candidate_id": request.candidate_id, "critical_tokens": critical_tokens},
        separators=(",", ":"),
    )
    return f"candidate-{sha256(payload.encode()).hexdigest()}"


def deterministic_quality_evaluator(
    request: RenderRequest,
    audio_bytes: bytes,
    diagnostics: AudioDiagnostics,
    critical_tokens: tuple[str, ...],
) -> CandidateQuality:
    """Return explicit deterministic-local quality evidence for one artifact.

    The local fixture uses the expected spoken text as its deterministic
    transcript. It is a stable offline quality adapter, not evidence that a
    hosted transcription provider was called.
    """
    if not isinstance(audio_bytes, bytes) or not audio_bytes:
        raise WorkflowContractError("quality evaluation requires audio bytes")
    fidelity = evaluate_critical_tokens(critical_tokens, request.expected_spoken_text)
    return CandidateQuality(
        candidate_id=request.candidate_id,
        fidelity=fidelity,
        diagnostics=diagnostics,
        pronunciation_passed=True,
        soft_score=Decimal("0"),
        transcription=TranscriptResult(
            text=request.expected_spoken_text,
            words=(),
            provider="local",
            model="deterministic-transcription-v1",
            usage=ProviderUsage(
                input_units=len(request.expected_spoken_text),
                output_units=len(audio_bytes),
            ),
            request_id=f"local-transcription-{request.candidate_id[10:22]}",
            checksum=sha256(audio_bytes).hexdigest(),
            cost=Decimal("0"),
            mode="deterministic-local",
        ),
    )


def host_local_quality_evaluator(
    request: RenderRequest,
    audio_bytes: bytes,
    diagnostics: AudioDiagnostics,
    critical_tokens: tuple[str, ...],
) -> CandidateQuality:
    """Return script-derived QA explicitly labeled as host-local evidence."""
    if request.provider != "host-local" or request.model != "host-local-tts-v1":
        raise WorkflowContractError(
            "host-local quality requires the host-local binding"
        )
    quality = deterministic_quality_evaluator(
        request, audio_bytes, diagnostics, critical_tokens
    )
    if quality.transcription is None:
        raise WorkflowContractError("host-local quality transcript is unavailable")
    return replace(
        quality,
        transcription=replace(
            quality.transcription,
            provider="host-local",
            model="host-local-tts-v1",
            request_id=f"host-local-transcription-{request.candidate_id[10:22]}",
            mode="host-local",
        ),
    )


def build_transcription_quality_evaluator(
    transcriber: Transcriber,
    *,
    transcription_records: FilesystemTranscriptionRecordStore | None = None,
    provider_evidence_recorder: ProviderEvidenceRecorder | None = None,
    provider_evidence_occurred_at: datetime | None = None,
    provider_evidence_clock: ProviderEvidenceClock | None = None,
) -> QualityEvaluator:
    """Build an evaluator that uses and durably records provider evidence."""
    if not hasattr(transcriber, "transcribe"):
        raise TypeError("transcriber must expose transcribe(audio)")
    if provider_evidence_recorder is not None and (
        provider_evidence_occurred_at is None and provider_evidence_clock is None
    ):
        raise TypeError("provider evidence recorder requires a timestamp source")
    if (
        provider_evidence_occurred_at is not None
        and provider_evidence_clock is not None
    ):
        raise TypeError("provider evidence cannot use two timestamp sources")

    async def evaluate(
        request: RenderRequest,
        audio_bytes: bytes,
        diagnostics: AudioDiagnostics,
        critical_tokens: tuple[str, ...],
    ) -> CandidateQuality:
        if not isinstance(audio_bytes, bytes) or not audio_bytes:
            raise TranscriptionFailureError("transcription requires audio bytes")
        audio_checksum = sha256(audio_bytes).hexdigest()
        started_ns = perf_counter_ns()
        if transcription_records is None:
            result = await _transcribe(transcriber, audio_bytes)
            replayed = False
        else:
            async with _transcription_claim(
                transcription_records, request.candidate_id
            ):
                result, replayed = await _find_or_transcribe(
                    transcription_records,
                    request.candidate_id,
                    transcriber,
                    audio_bytes,
                )
        _validate_transcript(result, audio_checksum)
        if provider_evidence_recorder is not None and not replayed:
            _record_provider_evidence(
                provider_evidence_recorder,
                ProviderEvidence(
                    operation="transcribe",
                    provider=result.provider,
                    request_id=result.request_id,
                    model=result.model,
                    input_sha256=audio_checksum,
                    output_sha256=sha256(result.text.encode("utf-8")).hexdigest(),
                    usage=provider_usage_to_mapping(result.usage),
                    currency="USD",
                    estimated_cost=result.cost,
                    reconciled_cost=None,
                    latency_ms=_elapsed_milliseconds(started_ns),
                    retry_count=request.attempt - 1,
                    occurred_at=_provider_evidence_timestamp(
                        provider_evidence_occurred_at, provider_evidence_clock
                    ),
                    evidence_kind=_evidence_kind(result.provider, result.mode),
                ),
            )
        fidelity = evaluate_critical_tokens(critical_tokens, result.text)
        return CandidateQuality(
            candidate_id=request.candidate_id,
            fidelity=fidelity,
            diagnostics=diagnostics,
            pronunciation_passed=True,
            soft_score=Decimal("0"),
            transcription=result,
        )

    return evaluate


def _elapsed_milliseconds(started_ns: int) -> int:
    """Return a non-negative whole-millisecond duration for provider evidence."""
    elapsed_ns = perf_counter_ns() - started_ns
    return max(0, (elapsed_ns + 999_999) // 1_000_000)


def _provider_evidence_timestamp(
    occurred_at: datetime | None, clock: ProviderEvidenceClock | None
) -> datetime:
    """Resolve the timestamp for one evidence record at the operation boundary."""
    value = clock() if clock is not None else occurred_at
    if value is None:
        raise WorkflowContractError("provider evidence timestamp is unavailable")
    return value


def _evidence_kind(provider: str, mode: str | None = None) -> str:
    """Map an adapter identity to the frozen evidence taxonomy."""
    if mode == "deterministic-local" or provider == "local":
        return "synthetic"
    if mode == "host-local" or provider == "host-local":
        return "host-local"
    return "provider-live"


def _record_provider_evidence(
    recorder: ProviderEvidenceRecorder, evidence: ProviderEvidence
) -> None:
    """Persist one evidence record and map recorder failures to a contract error."""
    try:
        recorder(evidence)
    except Exception as error:
        raise WorkflowContractError(
            "provider evidence could not be persisted"
        ) from error


async def _find_or_transcribe(
    records: FilesystemTranscriptionRecordStore,
    candidate_id: str,
    transcriber: Transcriber,
    audio_bytes: bytes,
) -> tuple[TranscriptResult, bool]:
    """Load existing evidence or atomically persist exactly one provider response."""
    try:
        persisted = records.find(candidate_id)
    except ArtifactIntegrityError as error:
        raise TranscriptionFailureError(
            "persisted transcription evidence is invalid"
        ) from error
    if persisted is not None:
        return persisted.result, True
    result = await _transcribe(transcriber, audio_bytes)
    _validate_transcript(result, sha256(audio_bytes).hexdigest())
    try:
        return records.save(candidate_id, result).result, False
    except (ArtifactIntegrityError, IdempotencyConflictError) as error:
        raise TranscriptionFailureError(
            "transcription evidence could not be persisted"
        ) from error


async def _transcribe(transcriber: Transcriber, audio_bytes: bytes) -> TranscriptResult:
    """Map provider failures to stable workflow transcription errors."""
    try:
        result = await transcriber.transcribe(audio_bytes)
    except (ProviderRateLimited, TimeoutError) as error:
        raise TranscriptionTransientError(
            "transcription provider temporarily unavailable"
        ) from error
    except (ProviderRequestFailed, ValueError, TypeError) as error:
        raise TranscriptionFailureError(
            "transcription provider returned invalid evidence"
        ) from error
    except TranscriptionTransientError:
        raise
    except Exception as error:
        raise TranscriptionFailureError("transcription provider failed") from error
    if not isinstance(result, TranscriptResult):
        raise TranscriptionFailureError("transcriber returned malformed evidence")
    return result


def _validate_transcript(result: TranscriptResult, audio_checksum: str) -> None:
    """Require non-empty evidence bound to the exact transcribed audio."""
    if not result.text.strip():
        raise TranscriptionFailureError("transcriber returned empty text")
    if result.checksum != audio_checksum:
        raise TranscriptionFailureError(
            "transcript checksum does not match audio bytes"
        )


def build_durable_render_activity(
    service: DurableRenderService,
    renderer: AudioRenderer,
    artifacts: FilesystemArtifactStore,
    *,
    quality_evaluator: QualityEvaluator | None = None,
    transcriber: Transcriber | None = None,
    quality_records: FilesystemQualityRecordStore | None = None,
    transcription_records: FilesystemTranscriptionRecordStore | None = None,
    provider_dispatch: ProviderActivityDispatchPolicy | None = None,
    provider_evidence_recorder: ProviderEvidenceRecorder | None = None,
    provider_evidence_recorder_factory: ProviderEvidenceRecorderFactory | None = None,
    provider_evidence_occurred_at: datetime | None = None,
    provider_evidence_clock: ProviderEvidenceClock | None = None,
) -> ActivityHandler:
    """Build a Temporal activity; non-local requests require explicit QA."""
    if not isinstance(service, DurableRenderService):
        raise TypeError("service must be DurableRenderService")
    if not isinstance(artifacts, FilesystemArtifactStore):
        raise TypeError("artifacts must be FilesystemArtifactStore")
    if quality_evaluator is not None and transcriber is not None:
        raise TypeError("provide quality_evaluator or transcriber, not both")
    if transcriber is not None and (
        quality_records is None or transcription_records is None
    ):
        raise TypeError(
            "provider-backed activity requires quality and transcription records"
        )
    if provider_dispatch is not None and not isinstance(
        provider_dispatch, ProviderActivityDispatchPolicy
    ):
        raise TypeError("provider_dispatch must be a ProviderActivityDispatchPolicy")
    if (
        provider_evidence_recorder is not None
        and provider_evidence_recorder_factory is not None
    ):
        raise TypeError("provide provider evidence recorder or factory, not both")
    if (
        (
            provider_evidence_recorder is not None
            or provider_evidence_recorder_factory is not None
        )
        and provider_evidence_occurred_at is None
        and provider_evidence_clock is None
    ):
        raise TypeError("provider evidence recorder requires a timestamp source")
    if (
        provider_evidence_occurred_at is not None
        and provider_evidence_clock is not None
    ):
        raise TypeError("provider evidence cannot use two timestamp sources")
    evaluator = quality_evaluator
    if (
        evaluator is None
        and transcriber is not None
        and provider_evidence_recorder_factory is None
    ):
        evaluator = build_transcription_quality_evaluator(
            transcriber,
            transcription_records=transcription_records,
            provider_evidence_recorder=provider_evidence_recorder,
            provider_evidence_occurred_at=provider_evidence_occurred_at,
            provider_evidence_clock=provider_evidence_clock,
        )

    @activity.defn(name=RENDER_SEGMENT_ACTIVITY_NAME)
    async def render_segment(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return await _run_render_activity(
                payload,
                service=service,
                renderer=renderer,
                artifacts=artifacts,
                quality_evaluator=evaluator,
                quality_records=quality_records,
                provider_dispatch=provider_dispatch,
                requires_transcription=transcriber is not None,
                provider_evidence_recorder=provider_evidence_recorder,
                provider_evidence_recorder_factory=provider_evidence_recorder_factory,
                provider_evidence_occurred_at=provider_evidence_occurred_at,
                provider_evidence_clock=provider_evidence_clock,
                transcriber=transcriber,
                transcription_records=transcription_records,
            )
        except ProviderDispatchRejectedError as error:
            raise ApplicationError(
                str(error),
                [reason.value for reason in error.decision.reasons],
                type=ProviderDispatchRejectedError.__name__,
                non_retryable=True,
            ) from error
        except RightsDeniedError as error:
            raise ApplicationError(
                str(error),
                type=RightsFailureError.__name__,
                non_retryable=True,
            ) from error
        except TranscriptionFailureError as error:
            raise ApplicationError(
                str(error),
                type=TranscriptionFailureError.__name__,
                non_retryable=True,
            ) from error
        except TranscriptionTransientError as error:
            raise ApplicationError(
                str(error),
                type=TranscriptionTransientError.__name__,
                non_retryable=False,
            ) from error
        except (
            ArtifactIntegrityError,
            AudioDiagnosticsError,
            RenderRejectedError,
        ) as error:
            raise ApplicationError(
                str(error),
                type=MalformedAudioError.__name__,
                non_retryable=True,
            ) from error
        except (CandidateSelectionError, WorkflowContractError) as error:
            raise ApplicationError(
                str(error),
                type=WorkflowContractError.__name__,
                non_retryable=True,
            ) from error

    return render_segment


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def build_publication_request_resolver(
    *,
    package_resolver: PublicationPackageResolver,
    target_resolver: PublicationTargetResolver,
    package_digest_resolver: PublicationPackageDigestResolver | None = None,
) -> PublicationRequestResolver:
    """Resolve a compact API handoff into worker-local immutable evidence.

    Temporal carries only references and authorization provenance. Package bytes,
    manifests, and target configuration remain in worker-owned stores.
    """
    if not callable(package_resolver) or not callable(target_resolver):
        raise TypeError("publication resolvers must be callable")

    def resolve(payload: dict[str, Any]) -> PublicationActivityRequest:
        if not isinstance(payload, dict):
            raise PublishingValidationError("publication activity payload is malformed")
        raw_payload: Mapping[str, object] = payload
        nested = payload.get("payload")
        if isinstance(nested, Mapping):
            raw_payload = nested

        def uuid7_value(name: str) -> UUID:
            try:
                value = UUID(str(raw_payload[name]))
            except (KeyError, TypeError, ValueError) as error:
                raise PublishingValidationError(f"{name} is invalid") from error
            if value.version != 7:
                raise PublishingValidationError(f"{name} must be UUIDv7")
            return value

        tenant_id = uuid7_value("tenant_id")
        project_id = uuid7_value("project_id")
        episode_id = uuid7_value("episode_id")
        target_id = raw_payload.get("target_id")
        idempotency_key = raw_payload.get("idempotency_key")
        package_reference = raw_payload.get("package_reference")
        authorization_value = raw_payload.get("authorization")
        if not isinstance(target_id, str) or not target_id.strip():
            raise PublishingValidationError("publication target_id is required")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise PublishingValidationError(
                "publication activity idempotency key is required"
            )
        if not isinstance(package_reference, Mapping):
            raise PublishingValidationError("publication package reference is required")
        if not isinstance(authorization_value, Mapping):
            raise PublishingValidationError("publication authorization is required")
        expected_episode_version_id = package_reference.get("episode_version_id")
        expected_package_sha256 = raw_payload.get("package_sha256")
        if expected_package_sha256 is None:
            expected_package_sha256 = package_reference.get("package_sha256")
        expected_manifest_sha256 = raw_payload.get("package_manifest_sha256")
        if expected_manifest_sha256 is None:
            expected_manifest_sha256 = package_reference.get("package_manifest_sha256")
        if not isinstance(expected_episode_version_id, str):
            raise PublishingValidationError(
                "publication package version reference is invalid"
            )
        if (
            not isinstance(expected_package_sha256, str)
            or _SHA256.fullmatch(expected_package_sha256) is None
        ):
            raise PublishingValidationError(
                "publication package checksum reference is invalid"
            )
        if (
            not isinstance(expected_manifest_sha256, str)
            or _SHA256.fullmatch(expected_manifest_sha256) is None
        ):
            raise PublishingValidationError(
                "publication manifest checksum reference is invalid"
            )

        package = package_resolver(expected_episode_version_id)
        if package is None:
            raise PublishingValidationError("publication package is unavailable")
        if package.episode_version_id != expected_episode_version_id:
            raise PublishingValidationError(
                "publication package version reference is inconsistent"
            )
        # The API and approval repository already bind the generic package-byte
        # digest. This worker boundary must not relabel the audio final checksum as
        # that digest; the canonical manifest digest below binds the resolved package.
        if manifest_sha256_for(package) != expected_manifest_sha256:
            raise PublishingValidationError("publication manifest checksum mismatched")
        if package_digest_resolver is not None:
            try:
                actual_package_sha256 = package_digest_resolver(package)
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                raise PublishingValidationError(
                    "publication package checksum is unavailable"
                ) from error
            if actual_package_sha256 != expected_package_sha256:
                raise PublishingValidationError(
                    "publication package checksum mismatched"
                )

        target = target_resolver(tenant_id, project_id, target_id)
        if target is None:
            raise PublishingValidationError("publication target is unavailable")
        if (
            not isinstance(target, PublicationTarget)
            or target.target_id != target_id
            or target.tenant_id != tenant_id
            or target.project_id != project_id
        ):
            raise PublishingValidationError("publication target is inconsistent")
        try:
            authorization = PublicationAuthorization(
                actor_id=authorization_value["actor_id"],
                decision_id=authorization_value["decision_id"],
                reason=authorization_value["reason"],
                operation=authorization_value.get("operation", "publish"),
            )
            return PublicationActivityRequest(
                tenant_id=tenant_id,
                project_id=project_id,
                episode_id=episode_id,
                package=package,
                target=target,
                authorization=authorization,
                idempotency_key=idempotency_key,
            )
        except KeyError as error:
            raise PublishingValidationError(
                "publication authorization is incomplete"
            ) from error
        except (TypeError, ValueError) as error:
            if isinstance(error, PublishingValidationError):
                raise
            raise PublishingValidationError(
                "publication authorization is malformed"
            ) from error

    return resolve


def build_durable_publication_activity(
    service: PublishingService,
    request_resolver: PublicationRequestResolver | None = None,
    *,
    activity_name: str = PUBLISH_EPISODE_ACTIVITY_NAME,
    production_output: bool = False,
) -> ActivityHandler:
    """Build the Temporal publication activity over the publishing service port."""
    if not isinstance(service, PublishingService):
        raise TypeError("service must be a PublishingService")
    if not isinstance(activity_name, str) or not activity_name.strip():
        raise TypeError("activity_name must be a non-empty string")
    resolver = request_resolver or PublicationActivityRequest.from_payload

    @activity.defn(name=activity_name)
    async def publish_episode(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            request = resolver(payload)
            if not isinstance(request, PublicationActivityRequest):
                raise PublishingValidationError(
                    "publication request resolver returned invalid data"
                )
            receipt = service.publish(
                request.package,
                request.target,
                request.authorization,
                request.idempotency_key,
            )
            result: dict[str, Any] = {"receipt": receipt.to_dict()}
            if production_output:
                result.update(
                    {
                        "episode_id": str(request.episode_id),
                        "episode_version_id": request.package.episode_version_id,
                        "passed": True,
                        "package_sha256": receipt.package_sha256,
                        "package_manifest_sha256": manifest_sha256_for(request.package),
                    }
                )
            return result
        except PublicationUncertainOutcomeError as error:
            raise ApplicationError(
                str(error),
                type=PublicationUncertainOutcomeError.__name__,
                non_retryable=True,
            ) from error
        except (PublicationConflictError, PublishingValidationError) as error:
            raise ApplicationError(
                str(error),
                type=type(error).__name__,
                non_retryable=True,
            ) from error

    return publish_episode


async def _run_render_activity(
    payload: dict[str, Any],
    *,
    service: DurableRenderService,
    renderer: AudioRenderer,
    artifacts: FilesystemArtifactStore,
    quality_evaluator: QualityEvaluator | None,
    quality_records: FilesystemQualityRecordStore | None,
    provider_dispatch: ProviderActivityDispatchPolicy | None,
    requires_transcription: bool,
    provider_evidence_recorder: ProviderEvidenceRecorder | None,
    provider_evidence_recorder_factory: ProviderEvidenceRecorderFactory | None,
    provider_evidence_occurred_at: datetime | None,
    provider_evidence_clock: ProviderEvidenceClock | None,
    transcriber: Transcriber | None,
    transcription_records: FilesystemTranscriptionRecordStore | None,
) -> dict[str, Any]:
    episode_input, segment, attempt, take, activity_key = _parse_payload(payload)
    scoped_provider_evidence_recorder = provider_evidence_recorder
    if provider_evidence_recorder_factory is not None:
        try:
            scoped_provider_evidence_recorder = provider_evidence_recorder_factory(
                episode_input
            )
        except Exception as error:
            raise WorkflowContractError(
                "provider evidence scope could not be established"
            ) from error
    expected_key = activity_key_for(
        episode_input,
        "render",
        segment.segment_id,
        attempt=attempt,
        take=take,
    )
    if activity_key != expected_key:
        raise WorkflowContractError("activity key does not match episode snapshot")

    request = replace(segment.render_request, attempt=attempt, take_index=take)
    if provider_dispatch is not None:
        provider_dispatch.require_render(request, segment.consent)
    service.preflight(request, segment.consent, renderer)
    scoped_evaluator = quality_evaluator
    if scoped_evaluator is None and transcriber is not None:
        scoped_evaluator = build_transcription_quality_evaluator(
            transcriber,
            transcription_records=transcription_records,
            provider_evidence_recorder=scoped_provider_evidence_recorder,
            provider_evidence_occurred_at=provider_evidence_occurred_at,
            provider_evidence_clock=provider_evidence_clock,
        )
    if scoped_evaluator is None:
        if request.provider != "local":
            raise WorkflowContractError(
                "non-local render requests require an explicit quality evaluator"
            )
        scoped_evaluator = deterministic_quality_evaluator
    if quality_records is not None:
        quality_key = _quality_cache_key(request, segment.critical_tokens)
    started_ns = perf_counter_ns()
    outcomes = await service.render_takes(
        request,
        segment.consent,
        renderer,
        take_count=1,
    )
    if len(outcomes) != 1:
        raise WorkflowContractError("render activity must produce one take")
    outcome = outcomes[0]
    if scoped_provider_evidence_recorder is not None and not outcome.replayed:
        _record_provider_evidence(
            scoped_provider_evidence_recorder,
            ProviderEvidence(
                operation="render",
                provider=outcome.candidate.provider,
                request_id=outcome.candidate.request_id,
                model=outcome.candidate.model,
                input_sha256=sha256(
                    request.expected_spoken_text.encode("utf-8")
                ).hexdigest(),
                output_sha256=outcome.candidate.artifact.sha256,
                usage=provider_usage_to_mapping(outcome.candidate.usage),
                currency="USD",
                estimated_cost=outcome.candidate.cost,
                reconciled_cost=None,
                latency_ms=_elapsed_milliseconds(started_ns),
                retry_count=request.attempt - 1,
                occurred_at=_provider_evidence_timestamp(
                    provider_evidence_occurred_at, provider_evidence_clock
                ),
                evidence_kind=_evidence_kind(outcome.candidate.provider),
            ),
        )
    if quality_records is not None:
        persisted_quality = quality_records.find(
            quality_key, expected_candidate_id=request.candidate_id
        )
        if persisted_quality is not None:
            return persisted_quality.to_dict()
    audio_bytes = artifacts.read(outcome.candidate.artifact)
    diagnostics = diagnose_wav(
        audio_bytes,
        expected_sample_rate_hz=request.sample_rate_hz,
        expected_channels=1,
    )
    if provider_dispatch is not None and requires_transcription:
        provider_dispatch.require_transcription(request, segment.consent)
    if scoped_evaluator is None:
        raise WorkflowContractError("render activity quality evaluator is unavailable")
    quality_value = scoped_evaluator(
        request,
        audio_bytes,
        diagnostics,
        segment.critical_tokens,
    )
    quality = await quality_value if isawaitable(quality_value) else quality_value
    if quality.candidate_id != outcome.candidate.candidate_id:
        raise WorkflowContractError("quality candidate does not match render candidate")
    if quality_records is not None:
        quality_records.save(quality, cache_key=quality_key)
    return quality.to_dict()


def _parse_payload(
    payload: dict[str, Any],
) -> tuple[EpisodeWorkflowInput, SegmentWorkflowInput, int, int, str]:
    if not isinstance(payload, dict):
        raise WorkflowContractError("render activity payload must be a mapping")
    episode_payload = payload.get("episode")
    segment_payload = payload.get("segment")
    if not isinstance(episode_payload, dict) or not isinstance(segment_payload, dict):
        raise WorkflowContractError("render activity payload snapshots are required")
    try:
        episode_input = EpisodeWorkflowInput.from_dict(episode_payload)
        segment = SegmentWorkflowInput.from_dict(segment_payload)
    except (TypeError, ValueError) as error:
        raise WorkflowContractError(
            "render activity payload snapshot is malformed"
        ) from error
    if segment not in episode_input.segments:
        raise WorkflowContractError("activity segment is not in episode snapshot")

    attempt = payload.get("attempt")
    take = payload.get("take")
    activity_key = payload.get("activity_key")
    if type(attempt) is not int or attempt <= 0:
        raise WorkflowContractError("activity attempt must be a positive integer")
    if type(take) is not int or take < 0:
        raise WorkflowContractError("activity take must be a non-negative integer")
    if not isinstance(activity_key, str) or not activity_key:
        raise WorkflowContractError("activity key must be a non-empty string")
    return episode_input, segment, attempt, take, activity_key


__all__ = [
    "ActivityHandler",
    "PublicationRequestResolver",
    "PublicationPackageDigestResolver",
    "ProviderActivityDispatchPolicy",
    "ProviderDispatchRejectedError",
    "ProviderEvidenceRecorder",
    "QualityEvaluator",
    "build_durable_render_activity",
    "build_durable_publication_activity",
    "build_publication_request_resolver",
    "build_transcription_quality_evaluator",
    "deterministic_quality_evaluator",
]
