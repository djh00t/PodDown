"""Packaged local API and Temporal worker runtime entrypoints."""

from __future__ import annotations

import asyncio
import json
import os
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, cast
from urllib.parse import urlparse
from uuid import UUID

from fastapi import FastAPI
from pydantic import SecretStr
from temporalio.client import Client
from temporalio.worker import Worker

from poddown.api import create_app
from poddown.api.temporal_dispatcher import TemporalClientTransport
from poddown.approvals import PostgresApprovalRepository, SQLiteApprovalRepository
from poddown.artifacts import FilesystemArtifactStore as PackageArtifactStore
from poddown.audio import (
    FINAL_MASTER_TRANSCRIPTION_ACTIVITY_NAME,
    MASTER_EPISODE_ACTIVITY_NAME,
    PACKAGE_EPISODE_ACTIVITY_NAME,
    PREPARE_CONTENT_ACTIVITY_NAME,
    PRODUCTION_PUBLISH_EPISODE_ACTIVITY_NAME,
    PUBLISH_EPISODE_ACTIVITY_NAME,
    ActivityHandler,
    AudioRenderer,
    DeterministicLocalRenderer,
    DurableRenderService,
    EpisodeCommandWorkflow,
    EpisodeProductionWorkflow,
    EpisodeRenderWorkflow,
    EpisodeWorkflowInput,
    LocalSpeechRenderer,
    MasteringService,
    ProductionWorkflowInput,
    ProviderActivityDispatchPolicy,
    build_durable_publication_activity,
    build_durable_render_activity,
    build_production_activities,
    build_publication_request_resolver,
    build_unavailable_production_activity,
    build_validate_source_activity,
    host_local_quality_evaluator,
)
from poddown.audio.prepare_activity import PrepareContentActivityInput
from poddown.audio.production_activities import (
    ProductionActivityDependencies,
    ProductionStageStore,
    ScriptDerivedTranscriber,
    spoken_text_for_prepared,
)
from poddown.audio.storage import (
    FilesystemArtifactStore,
    FilesystemQualityRecordStore,
    FilesystemRenderRecordStore,
    FilesystemTranscriptionRecordStore,
)
from poddown.content.live_adaptation import LiveAdaptationService
from poddown.content.persistence import (
    PreparedContentPersistenceError,
    PreparedContentStore,
)
from poddown.content.runtime import ConfiguredContentWorkflowSnapshotFactory
from poddown.content.service import ContentPreparationRequest, ContentPreparationResult
from poddown.durable_s3_object_storage import DurableS3ObjectStore
from poddown.nats_outbox import (
    ClosableNatsJetStreamClient,
    OutboxRelayWorkflow,
    build_nats_outbox_relay_activity,
)
from poddown.nats_runtime import NatsRuntimeSettings, connect_nats_jetstream
from poddown.object_maintenance import build_object_maintenance_activity
from poddown.packages import EpisodePackageService, package_sha256_for
from poddown.persistence import SQLiteUsageLedger, UsageEvent, UsageLedger
from poddown.postgres_ledger import PostgresUsageLedger
from poddown.postgres_objects import (
    PostgresObjectInventoryRepository,
    PostgresObjectReferenceRepository,
)
from poddown.postgres_persistence import PostgresEpisodeRepository
from poddown.postgres_publication import PostgresPublicationReceiptRepository
from poddown.postgres_receipts import PostgresCommandReceiptStore
from poddown.postgres_runtime import (
    ConnectionFactory,
    initialize_postgres,
    postgres_connection_factory,
)
from poddown.providers.contracts import ProviderEvidence, Transcriber
from poddown.providers.runtime import LiveProviderRuntime
from poddown.publishing import (
    DisclosurePolicy,
    FilesystemPublicationAdapter,
    PublicationAdapter,
    PublicationReceiptStore,
    PublicationTarget,
    PublishingService,
    S3CompatiblePublicationAdapter,
)
from poddown.resource_links import ResourceLinkSigner, ResourceReference
from poddown.runtime_snapshots import (
    ReferenceFixtureWorkflowSnapshotFactory,
    ReferenceRuntimeMode,
)
from poddown.s3_boto_transport import Boto3S3Transport
from poddown.s3_object_storage import (
    S3ObjectMaintenance,
    S3ObjectStore,
    S3StorageSettings,
)
from poddown.workflow_snapshots import (
    WorkflowRenderBinding,
    WorkflowSnapshotFactory,
    build_render_workflow_input,
)


class RuntimeConfigurationError(ValueError):
    """Required runtime configuration is absent or malformed."""


class RuntimeSnapshotFactory(WorkflowSnapshotFactory, Protocol):
    """Worker-facing snapshot factory capabilities beyond API composition."""

    @property
    def mode(self) -> Literal["deterministic-local", "host-local", "live-provider"]: ...

    @property
    def local_voice_bindings(self) -> Mapping[str, str]: ...

    @property
    def render_binding(self) -> WorkflowRenderBinding: ...

    def preparation_request_for(
        self, source_markdown: str, profile_id: str
    ) -> ContentPreparationRequest: ...


def _usage_evidence_recorder(
    ledger: UsageLedger,
    *,
    tenant_id: str,
    project_id: str,
    job_id: str,
) -> Callable[[ProviderEvidence], object]:
    """Bind provider evidence to one tenant-owned local usage ledger."""

    def record(evidence: ProviderEvidence) -> object:
        event = UsageEvent(
            tenant_id=UUID(tenant_id),
            project_id=UUID(project_id),
            job_id=UUID(job_id),
            provider_request_id=evidence.request_id,
            operation=evidence.operation,
            units=evidence.usage,
            currency=evidence.currency,
            estimated_cost=evidence.estimated_cost,
            reconciled_cost=evidence.reconciled_cost,
            created_at=evidence.occurred_at,
        )
        return ledger.record_evidence(evidence, event)

    return record


def _provider_usage_ledger(
    data_root: Path,
    *,
    require_postgres: bool,
) -> UsageLedger:
    """Select durable provider usage storage without an implicit fallback."""
    dsn = os.environ.get("PODDOWN_POSTGRES_DSN", "").strip()
    if dsn:
        return PostgresUsageLedger(postgres_connection_factory(dsn))
    if require_postgres:
        raise RuntimeConfigurationError(
            "PODDOWN_POSTGRES_DSN is required for live-provider usage evidence"
        )
    return SQLiteUsageLedger(data_root / "usage.sqlite3")


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    """Explicit Temporal endpoint, namespace, and task queue settings."""

    address: str
    namespace: str
    task_queue: str
    ready_file: str


@dataclass(frozen=True, slots=True)
class ApiRuntimeSettings:
    """Explicit local API runtime ports; no implicit in-memory fallback."""

    database_path: Path | None
    temporal_address: str
    temporal_namespace: str
    task_queue: str
    postgres_dsn: str | None = None


def api_runtime_settings() -> ApiRuntimeSettings:
    """Read the restart-safe local API settings from the environment."""
    database_value = os.environ.get("PODDOWN_SQLITE_PATH", "").strip()
    postgres_dsn = os.environ.get("PODDOWN_POSTGRES_DSN", "").strip()
    if bool(database_value) == bool(postgres_dsn):
        raise RuntimeConfigurationError(
            "configure exactly one of PODDOWN_SQLITE_PATH or PODDOWN_POSTGRES_DSN"
        )
    return ApiRuntimeSettings(
        database_path=Path(database_value) if database_value else None,
        temporal_address=_required("PODDOWN_TEMPORAL_ADDRESS"),
        temporal_namespace=os.environ.get(
            "PODDOWN_TEMPORAL_NAMESPACE", "default"
        ).strip()
        or "default",
        task_queue=_required("PODDOWN_TEMPORAL_TASK_QUEUE"),
        postgres_dsn=postgres_dsn or None,
    )


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeConfigurationError(f"{name} is required")
    return value


def worker_settings() -> WorkerSettings:
    """Read worker settings and fail closed before any network connection."""

    return WorkerSettings(
        address=_required("PODDOWN_TEMPORAL_ADDRESS"),
        namespace=os.environ.get("PODDOWN_TEMPORAL_NAMESPACE", "default").strip()
        or "default",
        task_queue=_required("PODDOWN_TEMPORAL_TASK_QUEUE"),
        ready_file=os.environ.get(
            "PODDOWN_WORKER_READY_FILE", "/tmp/poddown-worker-ready"
        ).strip()
        or "/tmp/poddown-worker-ready",
    )


def runtime_workflow_snapshot_factory() -> RuntimeSnapshotFactory | None:
    """Build the explicitly configured reference or production content composer."""
    root_value = os.environ.get("PODDOWN_WORKFLOW_FIXTURE_ROOT", "").strip()
    mode = os.environ.get("PODDOWN_WORKFLOW_MODE", "host-local").strip() or "host-local"
    raw_attempts = os.environ.get("PODDOWN_WORKFLOW_MAX_ATTEMPTS", "2").strip()
    try:
        max_attempts = int(raw_attempts)
        if mode not in {"deterministic-local", "host-local", "live-provider"}:
            raise ValueError("unsupported workflow mode")
        render_binding = None
        if mode == "live-provider":
            render_binding = LiveProviderRuntime.from_environment(
                os.environ
            ).render_binding
        if root_value:
            return ReferenceFixtureWorkflowSnapshotFactory(
                root_value,
                mode=cast(ReferenceRuntimeMode, mode),
                max_attempts=max_attempts,
                render_binding=render_binding,
            )
        configured_root = os.environ.get("PODDOWN_CONTENT_CONFIG_ROOT", "").strip()
        if not configured_root:
            return None
        return ConfiguredContentWorkflowSnapshotFactory(
            configured_root,
            mode=cast(
                Literal["deterministic-local", "host-local", "live-provider"],
                mode,
            ),
            max_attempts=max_attempts,
            render_binding=render_binding,
        )
    except (TypeError, ValueError) as error:
        raise RuntimeConfigurationError(
            "configured workflow content is not a valid runtime"
        ) from error


def _tcp_probe(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def _configured_probe(value: str, default_port: int) -> Callable[[], bool]:
    parsed = urlparse(value if "://" in value else f"//{value}")
    host = parsed.hostname
    port = parsed.port or default_port
    if not host:
        return lambda: False
    return lambda: _tcp_probe(host, port)


def runtime_dependency_probes() -> dict[str, Callable[[], bool]]:
    """Build bounded TCP probes; absent configuration is explicitly unhealthy."""

    endpoints = {
        "postgres": (os.environ.get("PODDOWN_POSTGRES_HOST", ""), 5432),
        "temporal": (os.environ.get("PODDOWN_TEMPORAL_ADDRESS", ""), 7233),
        "nats": (os.environ.get("PODDOWN_NATS_HOST", ""), 4222),
        "minio": (os.environ.get("PODDOWN_MINIO_ENDPOINT", ""), 9000),
    }
    return {
        name: _configured_probe(value, port) if value.strip() else (lambda: False)
        for name, (value, port) in endpoints.items()
    }


def _runtime_resource_components(
    connection_factory: ConnectionFactory | None,
) -> tuple[
    ResourceLinkSigner | None,
    Callable[[ResourceReference], bytes] | None,
]:
    """Compose signed resource links with the durable S3/reference ports."""
    secret = os.environ.get("PODDOWN_RESOURCE_LINK_SECRET", "").strip()
    base_url = os.environ.get("PODDOWN_RESOURCE_LINK_BASE_URL", "").strip()
    if not secret and not base_url:
        return None, None
    if not secret or not base_url:
        raise RuntimeConfigurationError(
            "PODDOWN_RESOURCE_LINK_SECRET and PODDOWN_RESOURCE_LINK_BASE_URL "
            "must be configured together"
        )
    raw_ttl = os.environ.get("PODDOWN_RESOURCE_LINK_TTL_SECONDS", "300").strip()
    try:
        ttl_seconds = int(raw_ttl)
        signer = ResourceLinkSigner(
            SecretStr(secret),
            base_url=base_url,
            ttl_seconds=ttl_seconds,
        )
    except (TypeError, ValueError) as error:
        raise RuntimeConfigurationError(
            "resource-link configuration is invalid"
        ) from error

    if connection_factory is None:
        dsn = os.environ.get("PODDOWN_POSTGRES_DSN", "").strip()
        if not dsn:
            raise RuntimeConfigurationError(
                "PODDOWN_POSTGRES_DSN is required for resource retrieval"
            )
        connection_factory = postgres_connection_factory(dsn)
        initialize_postgres(connection_factory)
    object_store, references, _inventory = _s3_runtime_components(
        connection_factory=connection_factory
    )

    def read_resource(reference: ResourceReference) -> bytes:
        object_reference = references.get(
            reference.tenant_id,
            reference.project_id,
            reference.sha256,
        )
        if object_reference.media_type != reference.media_type:
            raise RuntimeConfigurationError("resource media type is inconsistent")
        return object_store.read(
            reference.tenant_id,
            reference.project_id,
            object_reference,
        )

    return signer, read_resource


def build_api_app(
    settings: ApiRuntimeSettings | None = None,
    *,
    workflow_snapshot_factory: WorkflowSnapshotFactory | None = None,
) -> FastAPI:
    """Compose the local API with durable state and Temporal dispatch ports."""
    effective_settings = settings or api_runtime_settings()
    effective_factory = (
        workflow_snapshot_factory
        if workflow_snapshot_factory is not None
        else runtime_workflow_snapshot_factory()
    )
    available_profiles = getattr(effective_factory, "profile_names", None)
    if not isinstance(available_profiles, frozenset):
        available_profiles = None
    if effective_settings.postgres_dsn is not None:
        connection_factory = postgres_connection_factory(
            effective_settings.postgres_dsn
        )
        initialize_postgres(connection_factory)
        resource_link_signer, resource_reader = _runtime_resource_components(
            connection_factory
        )
        return create_app(
            episode_repository=PostgresEpisodeRepository(connection_factory),
            command_receipt_store=PostgresCommandReceiptStore(connection_factory),
            temporal_transport=TemporalClientTransport(
                effective_settings.temporal_address,
                namespace=effective_settings.temporal_namespace,
            ),
            temporal_task_queue=effective_settings.task_queue,
            approval_repository=PostgresApprovalRepository(connection_factory),
            health_probes=runtime_dependency_probes(),
            available_profiles=available_profiles,
            workflow_snapshot_factory=effective_factory,
            require_workflow_snapshot=True,
            resource_link_signer=resource_link_signer,
            resource_reader=resource_reader,
        )
    if effective_settings.database_path is None:
        raise RuntimeConfigurationError("a durable API database is required")
    resource_link_signer, resource_reader = _runtime_resource_components(None)
    return create_app(
        database_path=effective_settings.database_path,
        temporal_transport=TemporalClientTransport(
            effective_settings.temporal_address,
            namespace=effective_settings.temporal_namespace,
        ),
        temporal_task_queue=effective_settings.task_queue,
        approval_repository=SQLiteApprovalRepository(effective_settings.database_path),
        health_probes=runtime_dependency_probes(),
        available_profiles=available_profiles,
        workflow_snapshot_factory=effective_factory,
        require_workflow_snapshot=True,
        resource_link_signer=resource_link_signer,
        resource_reader=resource_reader,
    )


def _durable_render_activity() -> ActivityHandler:
    """Build the explicitly selected durable activity registered by the worker."""
    data_root = Path(os.environ.get("PODDOWN_RENDER_DATA_DIR", "/tmp/poddown-render"))
    artifacts = FilesystemArtifactStore(data_root / "artifacts")
    records = FilesystemRenderRecordStore(data_root / "render-records", artifacts)
    factory = runtime_workflow_snapshot_factory()
    requested_mode = os.environ.get("PODDOWN_WORKFLOW_MODE", "").strip()
    if factory is None and requested_mode:
        raise RuntimeConfigurationError(
            "PODDOWN_WORKFLOW_MODE requires configured workflow content"
        )
    renderer: AudioRenderer
    transcriber = None
    quality_records = None
    transcription_records = None
    provider_dispatch = None
    provider_evidence_recorder_factory = None
    provider_evidence_clock = None
    if factory is None or factory.mode == "deterministic-local":
        renderer = DeterministicLocalRenderer()
        quality_evaluator = None
    elif factory.mode == "host-local":
        renderer = LocalSpeechRenderer(voices=factory.local_voice_bindings)
        quality_evaluator = host_local_quality_evaluator
    else:
        live_runtime = LiveProviderRuntime.from_environment(os.environ)
        usage_ledger = _provider_usage_ledger(data_root, require_postgres=True)

        def provider_evidence_recorder_for(
            episode: EpisodeWorkflowInput,
        ) -> Callable[[ProviderEvidence], object]:
            if episode.tenant_id is None or episode.project_id is None:
                raise RuntimeConfigurationError(
                    "live render snapshot is missing tenant scope"
                )
            return _usage_evidence_recorder(
                usage_ledger,
                tenant_id=episode.tenant_id,
                project_id=episode.project_id,
                job_id=episode.episode_id,
            )

        renderer = live_runtime.renderer
        transcriber = live_runtime.transcriber
        quality_records = FilesystemQualityRecordStore(data_root / "quality-records")
        transcription_records = FilesystemTranscriptionRecordStore(
            data_root / "transcription-records"
        )
        provider_dispatch = ProviderActivityDispatchPolicy(
            preflight=live_runtime.preflight,
            route=live_runtime.settings.route,
            render_estimated_cost=live_runtime.render_estimated_cost,
            transcription_estimated_cost=live_runtime.transcription_estimated_cost,
        )
        quality_evaluator = None
        provider_evidence_recorder_factory = provider_evidence_recorder_for

        def provider_evidence_clock() -> datetime:
            return datetime.now(UTC)

    return build_durable_render_activity(
        DurableRenderService(artifacts, records),
        renderer,
        artifacts,
        quality_evaluator=quality_evaluator,
        transcriber=transcriber,
        quality_records=quality_records,
        transcription_records=transcription_records,
        provider_dispatch=provider_dispatch,
        provider_evidence_recorder_factory=provider_evidence_recorder_factory,
        provider_evidence_clock=provider_evidence_clock,
    )


def _durable_publication_activity(
    *, activity_name: str | None = None, production_output: bool = False
) -> ActivityHandler:
    """Build the explicitly selected publication activity over immutable bytes."""
    data_root = Path(os.environ.get("PODDOWN_RENDER_DATA_DIR", "/tmp/poddown-render"))
    package_artifacts = PackageArtifactStore(data_root / "package-artifacts")
    receipt_store, connection_factory = _publication_receipt_store(
        production_output=production_output
    )
    publication_mode = os.environ.get("PODDOWN_PUBLICATION_MODE", "filesystem").strip()
    adapter: PublicationAdapter
    if publication_mode == "filesystem":
        adapter = FilesystemPublicationAdapter(data_root / "published")
    elif publication_mode == "s3":
        adapter = _s3_publication_adapter(connection_factory=connection_factory)
    else:
        raise RuntimeConfigurationError(
            "PODDOWN_PUBLICATION_MODE must be filesystem or s3"
        )
    service = PublishingService(
        artifact_store=package_artifacts,
        adapters={publication_mode: adapter},
        receipt_store=receipt_store,
    )
    packages = EpisodePackageService(package_artifacts, data_root / "packages")
    return build_durable_publication_activity(
        service,
        request_resolver=build_publication_request_resolver(
            package_resolver=packages.get,
            target_resolver=_publication_target_resolver(),
            package_digest_resolver=lambda package: package_sha256_for(
                package, package_artifacts
            ),
        ),
        activity_name=activity_name or PUBLISH_EPISODE_ACTIVITY_NAME,
        production_output=production_output,
    )


def _publication_receipt_store(
    *, production_output: bool
) -> tuple[PublicationReceiptStore | None, ConnectionFactory | None]:
    """Compose durable receipts while preserving an unconfigured offline worker."""
    dsn = os.environ.get("PODDOWN_POSTGRES_DSN", "").strip()
    if not dsn:
        production_content_configured = bool(
            os.environ.get("PODDOWN_WORKFLOW_FIXTURE_ROOT", "").strip()
            or os.environ.get("PODDOWN_CONTENT_CONFIG_ROOT", "").strip()
        )
        if production_output and production_content_configured:
            raise RuntimeConfigurationError(
                "PODDOWN_POSTGRES_DSN is required for production publication receipts"
            )
        return None, None
    connection_factory = postgres_connection_factory(dsn)
    initialize_postgres(connection_factory)
    return PostgresPublicationReceiptRepository(connection_factory), connection_factory


def _production_publication_activity() -> ActivityHandler:
    """Build the optional-publish activity used by the complete production workflow."""
    return _durable_publication_activity(
        activity_name=PRODUCTION_PUBLISH_EPISODE_ACTIVITY_NAME,
        production_output=True,
    )


def _production_stage_activities() -> list[ActivityHandler]:
    """Register complete-workflow stage adapters for the configured fixture."""
    factory = runtime_workflow_snapshot_factory()
    data_root = Path(os.environ.get("PODDOWN_RENDER_DATA_DIR", "/tmp/poddown-render"))
    if factory is not None and hasattr(factory, "preparation_request_for"):
        render_artifacts = FilesystemArtifactStore(data_root / "artifacts")
        render_records = FilesystemRenderRecordStore(
            data_root / "render-records", render_artifacts
        )
        package_artifacts = PackageArtifactStore(data_root / "package-artifacts")
        packages = EpisodePackageService(
            package_artifacts,
            data_root / "packages",
        )
        prepared_store = PreparedContentStore(data_root / "prepared-content")
        usage_ledger = (
            _provider_usage_ledger(data_root, require_postgres=True)
            if factory.mode == "live-provider"
            else None
        )

        def provider_evidence_recorder_for(
            production: ProductionWorkflowInput,
        ) -> Callable[[ProviderEvidence], object]:
            if usage_ledger is None:
                raise RuntimeConfigurationError(
                    "provider evidence ledger is not configured"
                )
            raw = production.prepared_content_reference.get(
                "preparation_activity_input"
            )
            if not isinstance(raw, Mapping):
                raise RuntimeConfigurationError(
                    "production provider evidence scope is missing"
                )
            tenant_id = raw.get("tenant_id")
            project_id = raw.get("project_id")
            job_id = raw.get("episode_id")
            if (
                not isinstance(tenant_id, str)
                or not tenant_id
                or not isinstance(project_id, str)
                or not project_id
                or not isinstance(job_id, str)
                or not job_id
            ):
                raise RuntimeConfigurationError(
                    "production provider evidence scope is malformed"
                )
            return _usage_evidence_recorder(
                usage_ledger,
                tenant_id=tenant_id,
                project_id=project_id,
                job_id=job_id,
            )

        def prepared_content(
            production_input: ProductionWorkflowInput,
        ) -> ContentPreparationResult:
            prepared_key = production_input.prepared_content_reference.get(
                "prepared_content_key"
            )
            if isinstance(prepared_key, str):
                try:
                    prepared = prepared_store.load(prepared_key)
                except PreparedContentPersistenceError as error:
                    raise RuntimeConfigurationError(
                        "prepared content evidence is unavailable"
                    ) from error
            elif hasattr(factory, "prepared_content"):
                prepared = factory.prepared_content
            else:
                raise RuntimeConfigurationError(
                    "production runtime has no prepared-content store"
                )
            if (
                prepared.snapshot.source_sha256 != production_input.source_sha256
                or prepared.profile.profile_id != production_input.profile_id
            ):
                raise RuntimeConfigurationError(
                    "configured fixture does not match production content"
                )
            return prepared

        def record_prepared_content(
            value: object, prepared: ContentPreparationResult
        ) -> None:
            prepared_key = getattr(value, "prepared_content_key", None)
            if not isinstance(prepared_key, str) or not prepared_key:
                raise RuntimeConfigurationError(
                    "preparation context is missing prepared-content identity"
                )
            try:
                prepared_store.save(prepared_key, prepared)
            except PreparedContentPersistenceError as error:
                raise RuntimeConfigurationError(
                    "prepared content evidence could not be persisted"
                ) from error

        def transcriber_factory(
            production_input: ProductionWorkflowInput,
            prepared: ContentPreparationResult,
        ) -> Transcriber:
            if production_input.execution_mode == "live-provider":
                return LiveProviderRuntime.from_environment(os.environ).transcriber
            provider = (
                "host-local"
                if production_input.execution_mode == "host-local"
                else "local"
            )
            model = (
                "host-local-tts-v1"
                if production_input.execution_mode == "host-local"
                else "local-deterministic-v1"
            )
            return ScriptDerivedTranscriber(
                spoken_text_for_prepared(prepared),
                provider=provider,
                model=model,
                mode=production_input.execution_mode,
            )

        renderer_identity = (
            "host-local-tts-v1"
            if factory.mode == "host-local"
            else "eleven_tts"
            if factory.mode == "live-provider"
            else "local-deterministic-v1"
        )
        live_adaptation_factory = None
        if factory.mode == "live-provider":

            def live_adaptation(
                value: PrepareContentActivityInput,
            ) -> LiveAdaptationService:
                if usage_ledger is None:
                    raise RuntimeConfigurationError(
                        "provider evidence ledger is not configured"
                    )
                recorder = _usage_evidence_recorder(
                    usage_ledger,
                    tenant_id=value.tenant_id,
                    project_id=value.project_id,
                    job_id=value.episode_id,
                )
                return LiveProviderRuntime.from_environment(
                    os.environ,
                    reasoning_evidence_recorder=recorder,
                ).reasoning

            live_adaptation_factory = live_adaptation
        dependencies = ProductionActivityDependencies(
            render_artifacts=render_artifacts,
            render_records=render_records,
            stages=ProductionStageStore(data_root / "production-stages"),
            packages=packages,
            package_artifacts=package_artifacts,
            prepared_content=prepared_content,
            mastering=MasteringService(),
            transcriber_factory=transcriber_factory,
            renderer_identity=renderer_identity,
            provider_evidence_recorder_factory=(
                provider_evidence_recorder_for
                if factory.mode == "live-provider"
                else None
            ),
        )

        def render_snapshot(
            value: PrepareContentActivityInput, prepared: ContentPreparationResult
        ) -> str:
            if not hasattr(value, "episode_id"):
                raise RuntimeConfigurationError(
                    "preparation context is missing episode identity"
                )
            episode_version_id = getattr(value, "episode_version_id", None)
            if not isinstance(episode_version_id, str) or not episode_version_id:
                raise RuntimeConfigurationError(
                    "preparation context is missing episode version"
                )
            episode_id = value.episode_id
            if not isinstance(episode_id, str) or not episode_id:
                raise RuntimeConfigurationError(
                    "preparation context is missing episode identity"
                )
            return build_render_workflow_input(
                prepared,
                episode_id=episode_id,
                episode_version=episode_version_id,
                binding=factory.render_binding,
                tenant_id=value.tenant_id,
                project_id=value.project_id,
            ).to_json()

        production_activities = cast(
            list[ActivityHandler],
            list(
                build_production_activities(
                    dependencies,
                    lambda value: factory.preparation_request_for(
                        value.source_markdown, value.profile_id
                    ),
                    render_snapshot,
                    live_adaptation_factory,
                    record_prepared_content,
                )
            ),
        )
        return production_activities + [_production_publication_activity()]
    return [
        build_validate_source_activity(),
        build_unavailable_production_activity(PREPARE_CONTENT_ACTIVITY_NAME),
        build_unavailable_production_activity(MASTER_EPISODE_ACTIVITY_NAME),
        build_unavailable_production_activity(FINAL_MASTER_TRANSCRIPTION_ACTIVITY_NAME),
        build_unavailable_production_activity(PACKAGE_EPISODE_ACTIVITY_NAME),
        _production_publication_activity(),
    ]


def _publication_target_resolver() -> Callable[
    [UUID, UUID, str], PublicationTarget | None
]:
    """Load explicit worker target metadata without accepting secret values."""
    raw_value = os.environ.get("PODDOWN_PUBLICATION_TARGETS_JSON", "").strip()
    if not raw_value:
        return lambda _tenant_id, _project_id, _target_id: None
    try:
        raw_targets = json.loads(raw_value)
    except json.JSONDecodeError as error:
        raise RuntimeConfigurationError(
            "PODDOWN_PUBLICATION_TARGETS_JSON is malformed"
        ) from error
    if not isinstance(raw_targets, list) or not raw_targets:
        raise RuntimeConfigurationError(
            "PODDOWN_PUBLICATION_TARGETS_JSON must be a non-empty list"
        )
    targets: dict[tuple[UUID, UUID, str], PublicationTarget] = {}
    for raw_target in raw_targets:
        if not isinstance(raw_target, Mapping):
            raise RuntimeConfigurationError(
                "PODDOWN_PUBLICATION_TARGETS_JSON contains an invalid target"
            )
        disclosure = raw_target.get("disclosure", {})
        if not isinstance(disclosure, Mapping):
            raise RuntimeConfigurationError(
                "publication target disclosure must be an object"
            )
        try:
            target = PublicationTarget(
                tenant_id=UUID(str(raw_target["tenant_id"])),
                project_id=UUID(str(raw_target["project_id"])),
                target_id=raw_target["target_id"],
                kind=raw_target["kind"],
                secret_ref=raw_target["secret_ref"],
                show_id=raw_target["show_id"],
                feed_url=raw_target["feed_url"],
                disclosure=DisclosurePolicy(
                    spoken=disclosure.get("spoken", False),
                    show_notes=disclosure.get("show_notes", False),
                    platform=disclosure.get("platform", False),
                ),
                visibility=raw_target.get("visibility", "public"),
                update_policy=raw_target.get("update_policy", "immutable"),
            )
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise RuntimeConfigurationError(
                "PODDOWN_PUBLICATION_TARGETS_JSON contains an invalid target"
            ) from error
        key = (target.tenant_id, target.project_id, target.target_id)
        if key in targets:
            raise RuntimeConfigurationError(
                "PODDOWN_PUBLICATION_TARGETS_JSON contains a duplicate target"
            )
        targets[key] = target

    def resolve(
        tenant_id: UUID, project_id: UUID, target_id: str
    ) -> PublicationTarget | None:
        return targets.get((tenant_id, project_id, target_id))

    return resolve


def _s3_runtime_components(
    *, connection_factory: ConnectionFactory | None = None
) -> tuple[
    S3ObjectStore,
    PostgresObjectReferenceRepository,
    PostgresObjectInventoryRepository,
]:
    """Compose the S3 bytes and tenant-scoped PostgreSQL object ports.

    Credentials are resolved from the process environment at the transport
    boundary and never enter records or workflow payloads.
    """
    dsn = os.environ.get("PODDOWN_POSTGRES_DSN", "").strip()
    if not dsn:
        raise RuntimeConfigurationError("PODDOWN_POSTGRES_DSN is required")
    endpoint_value = _required("PODDOWN_MINIO_ENDPOINT")
    endpoint = endpoint_value if "://" in endpoint_value else f"http://{endpoint_value}"
    bucket = _required("PODDOWN_MINIO_BUCKET")
    secret_ref = _required("PODDOWN_MINIO_SECRET_REF")
    access_key = _required("PODDOWN_MINIO_ACCESS_KEY")
    secret_key = _required("PODDOWN_MINIO_SECRET_KEY")
    allow_insecure = os.environ.get("PODDOWN_MINIO_ALLOW_INSECURE_LOCAL", "0") == "1"
    if os.environ.get("PODDOWN_MINIO_ALLOW_INSECURE_LOCAL", "0") not in {"0", "1"}:
        raise RuntimeConfigurationError(
            "PODDOWN_MINIO_ALLOW_INSECURE_LOCAL must be 0 or 1"
        )
    settings = S3StorageSettings(
        endpoint=endpoint,
        bucket=bucket,
        secret_ref=secret_ref,
        region=os.environ.get("PODDOWN_MINIO_REGION", "us-east-1").strip()
        or "us-east-1",
        allow_insecure_local=allow_insecure,
    )

    def resolve(reference: str) -> tuple[str, str]:
        if reference != secret_ref:
            raise RuntimeConfigurationError("unknown MinIO secret reference")
        return access_key, secret_key

    blob_store = S3ObjectStore(
        Boto3S3Transport(settings, resolve),
        endpoint=settings.endpoint,
        bucket=settings.bucket,
        secret_ref=settings.secret_ref,
        region=settings.region,
        allow_insecure_local=settings.allow_insecure_local,
    )
    if connection_factory is None:
        connection_factory = postgres_connection_factory(dsn)
        initialize_postgres(connection_factory)
    references = PostgresObjectReferenceRepository(connection_factory)
    inventory = PostgresObjectInventoryRepository(connection_factory)
    return blob_store, references, inventory


def _s3_publication_adapter(
    *, connection_factory: ConnectionFactory | None = None
) -> S3CompatiblePublicationAdapter:
    """Compose MinIO publication with PostgreSQL object references."""
    blob_store, references, _inventory = _s3_runtime_components(
        connection_factory=connection_factory
    )
    return S3CompatiblePublicationAdapter(DurableS3ObjectStore(blob_store, references))


def _object_maintenance_activities() -> list[ActivityHandler]:
    """Register explicit S3 cleanup only when the operator opts it in."""
    raw_enabled = os.environ.get("PODDOWN_OBJECT_MAINTENANCE_ENABLED", "0").strip()
    if raw_enabled not in {"0", "1"}:
        raise RuntimeConfigurationError(
            "PODDOWN_OBJECT_MAINTENANCE_ENABLED must be 0 or 1"
        )
    if raw_enabled == "0":
        return []
    publication_mode = os.environ.get("PODDOWN_PUBLICATION_MODE", "filesystem").strip()
    if publication_mode != "s3":
        raise RuntimeConfigurationError(
            "object maintenance requires PODDOWN_PUBLICATION_MODE=s3"
        )
    blob_store, references, inventory = _s3_runtime_components()
    maintenance = S3ObjectMaintenance(
        blob_store,
        references=references,
        inventory=inventory,
    )
    return [build_object_maintenance_activity(maintenance)]


def _outbox_relay_activities() -> list[ActivityHandler]:
    """Register the explicit PostgreSQL-to-NATS relay activity when opted in."""
    raw_enabled = os.environ.get("PODDOWN_OUTBOX_RELAY_ENABLED", "0").strip()
    if raw_enabled not in {"0", "1"}:
        raise RuntimeConfigurationError("PODDOWN_OUTBOX_RELAY_ENABLED must be 0 or 1")
    if raw_enabled == "0":
        return []
    dsn = _required("PODDOWN_POSTGRES_DSN")
    raw_address = _required("PODDOWN_NATS_HOST")
    address = raw_address if "://" in raw_address else f"nats://{raw_address}"
    client_name = (
        os.environ.get("PODDOWN_NATS_CLIENT_NAME", "poddown-outbox-relay").strip()
        or "poddown-outbox-relay"
    )
    subject_prefix = (
        os.environ.get("PODDOWN_NATS_SUBJECT_PREFIX", "poddown.events").strip()
        or "poddown.events"
    )
    connection_factory = postgres_connection_factory(dsn)
    initialize_postgres(connection_factory)

    async def client_factory() -> ClosableNatsJetStreamClient:
        return await connect_nats_jetstream(
            NatsRuntimeSettings(address=address, client_name=client_name)
        )

    return [
        build_nats_outbox_relay_activity(
            connection_factory,
            client_factory,
            subject_prefix=subject_prefix,
        )
    ]


def api_main() -> None:
    """Serve the restart-safe API through uvicorn and explicit Temporal dispatch."""

    import uvicorn

    uvicorn.run(
        build_api_app(),
        host=os.environ.get("PODDOWN_API_HOST", "0.0.0.0"),
        port=int(os.environ.get("PODDOWN_API_PORT", "8000")),
    )


async def _run_worker() -> None:
    settings = worker_settings()
    ready_file = Path(settings.ready_file)
    ready_file.unlink(missing_ok=True)
    client = await Client.connect(settings.address, namespace=settings.namespace)
    worker = Worker(
        client,
        task_queue=settings.task_queue,
        workflows=[
            EpisodeCommandWorkflow,
            EpisodeRenderWorkflow,
            EpisodeProductionWorkflow,
            OutboxRelayWorkflow,
        ],
        activities=[
            _durable_render_activity(),
            _durable_publication_activity(),
            *_production_stage_activities(),
            *_object_maintenance_activities(),
            *_outbox_relay_activities(),
        ],
    )
    try:
        async with worker:
            await asyncio.sleep(0.1)
            if not worker.is_running:
                raise RuntimeConfigurationError("Temporal worker did not become ready")
            ready_file.write_text("ready\n")
            await asyncio.Future()
    finally:
        ready_file.unlink(missing_ok=True)


def worker_main() -> None:
    """Run the existing Temporal workflow/activity contracts."""

    asyncio.run(_run_worker())


__all__ = [
    "ApiRuntimeSettings",
    "RuntimeConfigurationError",
    "WorkerSettings",
    "api_main",
    "build_api_app",
    "api_runtime_settings",
    "initialize_postgres",
    "postgres_connection_factory",
    "worker_main",
    "runtime_dependency_probes",
    "runtime_workflow_snapshot_factory",
    "worker_settings",
]
