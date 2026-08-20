"""Offline FastAPI transport for the Episode Platform contract."""

from __future__ import annotations

import json
from collections.abc import Callable, Collection, Mapping
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import cast
from uuid import UUID

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from poddown.api.models import (
    CommandReceipt,
    EpisodeCreateRequest,
    EpisodeCreateResponse,
    EpisodeFailure,
    EpisodeStatusResponse,
    EpisodeSummary,
    PreviewRequest,
    PreviewResponse,
    ProblemDetail,
    ProductionStage,
    PublishCommandRequest,
    RenderCommandRequest,
    RequestContext,
    ResourceLink,
)
from poddown.api.runtime import (
    CommandDispatcher,
    CommandName,
    InMemoryCommandDispatcher,
)
from poddown.api.temporal_dispatcher import (
    CommandReceiptStore,
    TemporalCommandDispatcher,
    TemporalCommandTransport,
)
from poddown.approvals import (
    ApprovalAlreadyConsumed,
    ApprovalError,
    ApprovalRepository,
)
from poddown.auth import (
    AuthenticatedPrincipal,
    AuthenticationError,
    AuthSettings,
    LocalHeaderPrincipalResolver,
    OIDCPrincipalVerifier,
    PrincipalVerifier,
)
from poddown.episode_service import (
    EpisodeApplicationService,
    EpisodeCreateCommand,
    EpisodeRecord,
    EpisodeRepository,
    EpisodeServiceError,
    EpisodeState,
    InMemoryEpisodeRepository,
    InvalidEpisodeTransition,
    StructuredFailure,
)
from poddown.persistence import SQLiteCommandDispatcher, SQLiteEpisodeRepository
from poddown.preview import PreviewValidationError, preview_markdown
from poddown.production_readiness import DependencyState, HealthEvaluator
from poddown.resource_links import (
    ResourceLinkError,
    ResourceLinkSigner,
    ResourceReference,
)
from poddown.workflow_snapshots import (
    WorkflowSnapshotError,
    WorkflowSnapshotFactory,
    bind_snapshot_to_record,
)

_PROBLEM_MEDIA_TYPE = "application/problem+json"
_DEFAULT_PROFILES = frozenset({"default", "technical-dialogue"})


class ApiProblem(EpisodeServiceError):
    """Stable client error raised at the HTTP boundary."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status: int = 400,
    ) -> None:
        super().__init__(
            StructuredFailure(
                code=code,
                stage="api",
                message=message,
                retriable=False,
                details={},
                status=status,
            )
        )


def _problem_response(failure: StructuredFailure) -> JSONResponse:
    """Render stable problem JSON without raw internal details."""
    code = failure.code
    if code == "episode_validation_failed":
        if "profile" in failure.message.casefold():
            code = "invalid_profile"
        else:
            code = "invalid_source"
    if code == "publish_not_authorized":
        code = "publish_authorization_required"
    problem = ProblemDetail.from_exception(
        status=failure.status,
        code=code,
        detail=failure.message,
    )
    return JSONResponse(
        status_code=failure.status,
        content=problem.model_dump(mode="json"),
        media_type=_PROBLEM_MEDIA_TYPE,
    )


def _parse_context(
    *,
    tenant_header: str | None,
    project_header: str | None,
    idempotency_header: str | None,
) -> RequestContext:
    """Parse required demo headers with stable field-specific errors."""
    values = {
        "X-Tenant-ID": tenant_header,
        "X-Project-ID": project_header,
        "Idempotency-Key": idempotency_header,
    }
    for field, value in values.items():
        if value is None or not value.strip():
            code = {
                "X-Tenant-ID": "missing_tenant_id",
                "X-Project-ID": "missing_project_id",
                "Idempotency-Key": "missing_idempotency_key",
            }[field]
            raise ApiProblem(code=code, message=f"{field} is required")
    assert tenant_header is not None
    assert project_header is not None
    assert idempotency_header is not None
    try:
        context = RequestContext.from_headers(
            {
                "X-Tenant-ID": tenant_header,
                "X-Project-ID": project_header,
                "Idempotency-Key": idempotency_header,
            }
        )
    except ValidationError as error:
        message = str(error).casefold()
        if "tenant_id" in message:
            code = "invalid_tenant_id"
        elif "project_id" in message:
            code = "invalid_project_id"
        else:
            code = "invalid_idempotency_key"
        raise ApiProblem(code=code, message="request context is invalid") from error
    if context.tenant_id.version != 7:
        raise ApiProblem(code="invalid_tenant_id", message="tenant context is invalid")
    if context.project_id.version != 7:
        raise ApiProblem(
            code="invalid_project_id", message="project context is invalid"
        )
    return context


def _authenticated_context(
    *,
    settings: AuthSettings,
    verifier: PrincipalVerifier | None,
    local_resolver: LocalHeaderPrincipalResolver | None,
    tenant_header: str | None,
    project_header: str | None,
    idempotency_header: str | None,
    authorization_header: str | None,
    scope: str,
) -> tuple[RequestContext, AuthenticatedPrincipal]:
    """Authenticate a request and derive tenant/project scope from trusted context."""
    if settings.mode == "local":
        if local_resolver is None:
            raise ApiProblem(
                code="authentication_unavailable",
                message="local authentication is unavailable",
                status=503,
            )
        context = _parse_context(
            tenant_header=tenant_header,
            project_header=project_header,
            idempotency_header=idempotency_header,
        )
        try:
            principal = local_resolver.resolve(
                tenant_header=tenant_header,
                project_header=project_header,
            )
        except AuthenticationError as error:
            raise ApiProblem(
                code=error.code,
                message="authentication failed",
                status=401,
            ) from error
    else:
        if tenant_header is not None or project_header is not None:
            raise ApiProblem(
                code="scope_headers_forbidden",
                message="tenant and project headers are local-mode compatibility only",
                status=400,
            )
        if verifier is None:
            raise ApiProblem(
                code="authentication_unavailable",
                message="OIDC authentication is unavailable",
                status=503,
            )
        try:
            if authorization_header is None:
                raise AuthenticationError("authorization_missing")
            principal = verifier.verify(authorization_header)
        except AuthenticationError as error:
            raise ApiProblem(
                code=error.code,
                message="authentication failed",
                status=401,
            ) from error
        if len(principal.project_ids) != 1:
            raise ApiProblem(
                code="project_scope_required",
                message="one project scope is required for this API operation",
                status=403,
            )
        try:
            context = RequestContext(
                tenant_id=principal.tenant_id,
                project_id=next(iter(principal.project_ids)),
                idempotency_key=idempotency_header or "",
            )
        except ValidationError as error:
            if idempotency_header is None or not idempotency_header.strip():
                raise ApiProblem(
                    code="missing_idempotency_key",
                    message="Idempotency-Key is required",
                ) from error
            raise ApiProblem(
                code="invalid_idempotency_key",
                message="request context is invalid",
            ) from error
    if not principal.has_scope(scope):
        raise ApiProblem(
            code="scope_forbidden",
            message="authenticated principal lacks the required scope",
            status=403,
        )
    return context, principal


async def _parse_create_body(request: Request) -> EpisodeCreateRequest:
    """Decode and validate JSON while distinguishing invalid source encoding."""
    raw = await request.body()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ApiProblem(
            code="invalid_source_encoding",
            message="request body is not valid UTF-8",
        ) from error
    try:
        payload = json.loads(text)
        return EpisodeCreateRequest.model_validate(payload)
    except json.JSONDecodeError as error:
        raise ApiProblem(
            code="invalid_source", message="request source is invalid"
        ) from error
    except ValidationError as error:
        fields = {
            str(location[0])
            for item in error.errors()
            for location in [item.get("loc", ())]
            if location
        }
        if "profile" in fields:
            raise ApiProblem(
                code="invalid_profile",
                message="profile is invalid",
                status=422,
            ) from error
        raise ApiProblem(
            code="invalid_source", message="request source is invalid"
        ) from error


async def _parse_preview_body(request: Request) -> PreviewRequest:
    """Decode preview JSON while keeping source and validation errors redacted."""
    raw = await request.body()
    try:
        payload = json.loads(raw.decode("utf-8"))
        return PreviewRequest.model_validate(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
        fields = {
            str(location[0])
            for item in getattr(error, "errors", lambda: ())()
            for location in [item.get("loc", ())]
            if location
        }
        code = "invalid_profile" if "profile" in fields else "invalid_source"
        raise ApiProblem(
            code=code,
            message="profile is invalid"
            if code == "invalid_profile"
            else "request source is invalid",
            status=422,
        ) from error


async def _parse_publish_body(request: Request) -> PublishCommandRequest | None:
    """Decode an optional publish body while preserving local header compatibility."""
    raw = await request.body()
    if not raw.strip():
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
        return PublishCommandRequest.model_validate(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
        raise ApiProblem(
            code="invalid_publish_request",
            message="publish request is invalid",
            status=422,
        ) from error


async def _parse_render_body(request: Request) -> RenderCommandRequest | None:
    """Decode optional render controls without accepting ambiguous values."""
    raw = await request.body()
    if not raw.strip():
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
        return RenderCommandRequest.model_validate(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
        raise ApiProblem(
            code="invalid_render_request",
            message="render request is invalid",
            status=422,
        ) from error


def _workflow_command_payload(
    *,
    factory: WorkflowSnapshotFactory | None,
    required: bool,
    record: EpisodeRecord,
    command: CommandName,
    payload: Mapping[str, object] | None,
) -> Mapping[str, object] | None:
    """Bind an optional command payload to an immutable source snapshot."""
    if factory is None:
        if required:
            raise ApiProblem(
                code="workflow_snapshot_unavailable",
                message="an immutable workflow snapshot factory is not configured",
                status=503,
            )
        return payload
    try:
        snapshot = bind_snapshot_to_record(
            factory.build(record=record, command=command, payload=payload), record
        )
    except WorkflowSnapshotError as error:
        raise ApiProblem(
            code="workflow_snapshot_unavailable",
            message="an immutable workflow snapshot could not be composed",
            status=503,
        ) from error
    bound_payload = dict(payload or {})
    bound_payload.update(snapshot.to_payload())
    return bound_payload


def _publication_handoff_payload(
    *,
    record: EpisodeRecord,
    context: RequestContext,
    principal: AuthenticatedPrincipal,
    request: PublishCommandRequest,
) -> dict[str, object]:
    """Build the compact API-to-worker publication handoff.

    The command carries immutable references and authorization provenance only;
    package bytes, manifests, target configuration, and secrets stay in the worker.
    """
    if record.package_sha256 is None or record.package_manifest_sha256 is None:
        raise ApiProblem(
            code="package_not_ready",
            message="package and manifest checksums are required before publication",
            status=409,
        )
    return {
        "target_id": request.target_id,
        "approval_id": str(request.approval_id),
        "tenant_id": str(context.tenant_id),
        "project_id": str(context.project_id),
        "episode_id": str(record.episode_id),
        "idempotency_key": context.idempotency_key,
        "package_reference": {
            # The current episode repository has one immutable version identity;
            # the durable version repository will replace this with its version ID.
            "episode_version_id": str(record.episode_id),
            "package_sha256": record.package_sha256,
            "package_manifest_sha256": record.package_manifest_sha256,
        },
        "authorization": {
            "actor_id": principal.subject,
            "decision_id": str(request.approval_id),
            "reason": "scoped publication approval",
            "operation": "publish",
        },
    }


def _scoped_episode(
    service: EpisodeApplicationService,
    *,
    tenant_id: UUID,
    project_id: UUID,
    episode_id: str,
) -> EpisodeRecord:
    """Read one episode while hiding cross-tenant and cross-project records."""
    try:
        parsed_id = UUID(episode_id)
    except ValueError as error:
        raise ApiProblem(
            code="episode_not_found",
            message="episode was not found",
            status=404,
        ) from error
    if parsed_id.version != 7:
        raise ApiProblem(
            code="episode_not_found",
            message="episode was not found",
            status=404,
        )
    record = service.get_episode(tenant_id, parsed_id)
    if record.project_id != project_id:
        raise ApiProblem(
            code="episode_not_found",
            message="episode was not found",
            status=404,
        )
    return record


def _summary(
    record: EpisodeRecord,
    *,
    resource_link_signer: ResourceLinkSigner | None = None,
) -> EpisodeSummary:
    """Map the internal record to a source-safe API summary."""
    resources: list[ResourceLink] = []
    if resource_link_signer is not None and record.package_manifest_sha256 is not None:
        resources.append(
            ResourceLink(
                uri=resource_link_signer.issue(
                    tenant_id=record.tenant_id,
                    project_id=record.project_id,
                    episode_id=record.episode_id,
                    resource="manifest",
                    media_type="application/json",
                    sha256=record.package_manifest_sha256,
                    now=datetime.now(UTC),
                ),
                mime_type="application/json",
            )
        )
    return EpisodeSummary(
        id=record.episode_id,
        tenant_id=record.tenant_id,
        project_id=record.project_id,
        version=record.version,
        state=record.state.value,
        profile=record.profile_name,
        source_sha256=record.source_sha256,
        source_bytes=record.source_bytes,
        created_at=record.created_at,
        updated_at=record.updated_at,
        resources=resources,
    )


def _progress(state: EpisodeState) -> float:
    """Expose a deterministic coarse progress value for status polling."""
    return {
        EpisodeState.VALIDATED: 0.0,
        EpisodeState.SCRIPTED: 0.2,
        EpisodeState.RENDERED: 0.5,
        EpisodeState.QA_PASSED: 0.7,
        EpisodeState.PACKAGED: 0.9,
        EpisodeState.PUBLISHED: 1.0,
        EpisodeState.FAILED: 1.0,
    }[state]


def _status_stage(state: EpisodeState) -> ProductionStage:
    """Map legacy service states to the frozen production status stages."""
    return cast(
        ProductionStage,
        {
            EpisodeState.VALIDATED: "ingested",
            EpisodeState.SCRIPTED: "prepared",
            EpisodeState.RENDERED: "rendering",
            EpisodeState.QA_PASSED: "qa",
            EpisodeState.PACKAGED: "packaged",
            EpisodeState.PUBLISHED: "published",
            EpisodeState.FAILED: "failed",
        }[state],
    )


def _safe_failure(failure: StructuredFailure) -> EpisodeFailure:
    """Allowlist failure fields safe for polling clients."""
    return EpisodeFailure(
        code=failure.code,
        stage=failure.stage,
        status=failure.status,
        retriable=failure.retriable,
    )


def create_app(
    service: EpisodeApplicationService | None = None,
    *,
    dispatcher: CommandDispatcher | None = None,
    temporal_transport: TemporalCommandTransport | None = None,
    temporal_task_queue: str | None = None,
    available_profiles: Collection[str] | None = None,
    database_path: Path | str | None = None,
    episode_repository: EpisodeRepository | None = None,
    command_receipt_store: CommandReceiptStore | None = None,
    health_dependencies: Mapping[str, DependencyState] | None = None,
    health_probes: Mapping[str, Callable[[], bool]] | None = None,
    auth_settings: AuthSettings | None = None,
    principal_verifier: PrincipalVerifier | None = None,
    approval_repository: ApprovalRepository | None = None,
    workflow_snapshot_factory: WorkflowSnapshotFactory | None = None,
    require_workflow_snapshot: bool = False,
    resource_link_signer: ResourceLinkSigner | None = None,
    resource_reader: Callable[[ResourceReference], bytes] | None = None,
) -> FastAPI:
    """Create an offline or restart-safe app with injected lifecycle ports."""
    if service is not None and (
        database_path is not None or episode_repository is not None
    ):
        raise ValueError("service and persistence repository are mutually exclusive")
    if database_path is not None and episode_repository is not None:
        raise ValueError("database_path and episode_repository are mutually exclusive")
    if (temporal_transport is None) != (temporal_task_queue is None):
        raise ValueError(
            "temporal_transport and temporal_task_queue must be configured together"
        )
    profiles = frozenset(
        _DEFAULT_PROFILES if available_profiles is None else available_profiles
    )
    if service is not None:
        application_service = service
    elif episode_repository is not None:
        application_service = EpisodeApplicationService(
            repository=episode_repository,
            available_profiles=profiles,
        )
    elif database_path is not None:
        application_service = EpisodeApplicationService(
            repository=SQLiteEpisodeRepository(database_path),
            available_profiles=profiles,
        )
    else:
        application_service = EpisodeApplicationService(
            repository=InMemoryEpisodeRepository(),
            available_profiles=profiles,
        )
    sqlite_receipt_store = (
        SQLiteCommandDispatcher(database_path) if database_path is not None else None
    )
    temporal_receipt_store: CommandReceiptStore | None = command_receipt_store
    if temporal_receipt_store is None and sqlite_receipt_store is not None:
        temporal_receipt_store = cast(CommandReceiptStore, sqlite_receipt_store)
    command_dispatcher = (
        dispatcher
        if dispatcher is not None
        else (
            TemporalCommandDispatcher(
                temporal_transport,
                task_queue=temporal_task_queue,
                receipt_store=temporal_receipt_store,
            )
            if temporal_transport is not None and temporal_task_queue is not None
            else (
                sqlite_receipt_store
                if sqlite_receipt_store is not None
                else InMemoryCommandDispatcher()
            )
        )
    )
    app = FastAPI(
        title="PodDown Episode API", version="v1", docs_url=None, redoc_url=None
    )
    health_evaluator = HealthEvaluator()
    dependency_states = {} if health_dependencies is None else dict(health_dependencies)
    dependency_probes = {} if health_probes is None else dict(health_probes)
    effective_auth_settings = (
        auth_settings if auth_settings is not None else AuthSettings.from_environment()
    )
    oidc_verifier = (
        principal_verifier
        if effective_auth_settings.mode == "api" and principal_verifier is not None
        else (
            OIDCPrincipalVerifier(effective_auth_settings)
            if effective_auth_settings.mode == "api"
            else None
        )
    )
    local_resolver = (
        LocalHeaderPrincipalResolver(effective_auth_settings)
        if effective_auth_settings.mode == "local"
        else None
    )

    @app.get("/health/live")
    def health_live() -> dict[str, object]:
        return {"status": "healthy"}

    @app.get("/health/ready")
    def health_ready() -> dict[str, object]:
        snapshot = health_evaluator.evaluate(
            liveness=True, dependencies=dependency_states, probes=dependency_probes
        )
        content = {"status": snapshot.readiness, **snapshot.to_dict()}
        if snapshot.readiness != "healthy":
            return JSONResponse(status_code=503, content=content)  # type: ignore[return-value]
        return content

    @app.get("/health/dependencies")
    def health_dependency_status() -> dict[str, object]:
        return health_evaluator.evaluate(
            liveness=True, dependencies=dependency_states, probes=dependency_probes
        ).to_dict()

    @app.get(
        "/v1/resources/{resource_tenant_id}/{resource_project_id}/"
        "{resource_episode_id}/{resource_kind}"
    )
    def get_resource(
        request: Request,
        resource_tenant_id: str,
        resource_project_id: str,
        resource_episode_id: str,
        resource_kind: str,
        x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
        x_project_id: str | None = Header(default=None, alias="X-Project-ID"),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> Response:
        """Serve one verified resource without exposing storage internals."""
        context, _principal = _authenticated_context(
            settings=effective_auth_settings,
            verifier=oidc_verifier,
            local_resolver=local_resolver,
            tenant_header=x_tenant_id,
            project_header=x_project_id,
            idempotency_header="resource-read",
            authorization_header=authorization,
            scope="resources:read",
        )
        if resource_link_signer is None:
            raise ApiProblem(
                code="resource_links_unavailable",
                message="resource links are unavailable",
                status=503,
            )
        try:
            tenant_id = UUID(resource_tenant_id)
            project_id = UUID(resource_project_id)
            episode_id = UUID(resource_episode_id)
        except ValueError as error:
            raise ApiProblem(
                code="resource_not_found",
                message="resource was not found",
                status=404,
            ) from error
        if (tenant_id, project_id) != (context.tenant_id, context.project_id):
            raise ApiProblem(
                code="resource_not_found",
                message="resource was not found",
                status=404,
            )
        try:
            reference = resource_link_signer.verify(
                str(request.url),
                tenant_id=tenant_id,
                project_id=project_id,
                episode_id=episode_id,
                now=datetime.now(UTC),
            )
        except ResourceLinkError as error:
            raise ApiProblem(
                code="resource_not_found",
                message="resource was not found",
                status=404,
            ) from error
        if reference.resource != resource_kind:
            raise ApiProblem(
                code="resource_not_found",
                message="resource was not found",
                status=404,
            )
        if resource_reader is None:
            raise ApiProblem(
                code="resource_store_unavailable",
                message="resource storage is unavailable",
                status=503,
            )
        try:
            data = resource_reader(reference)
        except Exception as error:
            raise ApiProblem(
                code="resource_store_unavailable",
                message="resource storage is unavailable",
                status=503,
            ) from error
        if not isinstance(data, bytes) or sha256(data).hexdigest() != reference.sha256:
            raise ApiProblem(
                code="resource_integrity_failed",
                message="resource integrity verification failed",
                status=502,
            )
        return Response(
            content=data,
            media_type=reference.media_type,
            headers={"Cache-Control": "private, no-store"},
        )

    @app.exception_handler(EpisodeServiceError)
    async def handle_service_error(
        _request: Request,
        error: EpisodeServiceError,
    ) -> JSONResponse:
        return _problem_response(error.failure)

    @app.post("/v1/episodes", status_code=202)
    async def create_episode(
        request: Request,
        x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
        x_project_id: str | None = Header(default=None, alias="X-Project-ID"),
        idempotency_key: str | None = Header(
            default=None,
            alias="Idempotency-Key",
        ),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> EpisodeCreateResponse:
        context, _principal = _authenticated_context(
            settings=effective_auth_settings,
            verifier=oidc_verifier,
            local_resolver=local_resolver,
            tenant_header=x_tenant_id,
            project_header=x_project_id,
            idempotency_header=idempotency_key,
            authorization_header=authorization,
            scope="episodes:write",
        )
        payload = await _parse_create_body(request)
        if payload.profile not in profiles:
            raise ApiProblem(
                code="invalid_profile",
                message="profile is invalid",
                status=422,
            )
        record = application_service.create_episode(
            EpisodeCreateCommand(
                tenant_id=context.tenant_id,
                project_id=context.project_id,
                idempotency_key=context.idempotency_key,
                source_bytes=payload.source.encode("utf-8"),
                profile_name=payload.profile,
            )
        )
        command_payload = _workflow_command_payload(
            factory=workflow_snapshot_factory,
            required=require_workflow_snapshot,
            record=record,
            command="create",
            payload=None,
        )
        receipt = command_dispatcher.submit(
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            episode_id=record.episode_id,
            command="create",
            idempotency_key=context.idempotency_key,
            payload=command_payload,
        )
        return EpisodeCreateResponse(episode=_summary(record), receipt=receipt)

    @app.post("/v1/preview", response_model=PreviewResponse)
    async def preview_episode(
        request: Request,
        x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
        x_project_id: str | None = Header(default=None, alias="X-Project-ID"),
        idempotency_key: str | None = Header(
            default=None,
            alias="Idempotency-Key",
        ),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> PreviewResponse:
        """Validate source locally without dispatching provider or workflow work."""
        _context, _principal = _authenticated_context(
            settings=effective_auth_settings,
            verifier=oidc_verifier,
            local_resolver=local_resolver,
            tenant_header=x_tenant_id,
            project_header=x_project_id,
            idempotency_header=idempotency_key,
            authorization_header=authorization,
            scope="episodes:read",
        )
        payload = await _parse_preview_body(request)
        if payload.profile is not None and payload.profile not in profiles:
            raise ApiProblem(
                code="invalid_profile",
                message="profile is invalid",
                status=422,
            )
        try:
            result = preview_markdown(
                payload.source.encode("utf-8"),
                source_name="api-preview.md",
                flag_profile=payload.profile,
                project_config={"profiles": profiles},
                user_config=None,
            )
        except PreviewValidationError as error:
            code = (
                "invalid_profile"
                if any("profile" in message.casefold() for message in error.errors)
                else "invalid_source"
            )
            raise ApiProblem(
                code=code,
                message="profile is invalid"
                if code == "invalid_profile"
                else "request source is invalid",
                status=422,
            ) from error
        return PreviewResponse(
            source_sha256=result.source_sha256,
            profile_id=result.profile_id,
            source_bytes=result.source_bytes,
            block_count=result.block_count,
            provider_calls=result.provider_calls,
        )

    @app.get("/v1/episodes/{episode_id}", response_model=EpisodeSummary)
    def get_episode(
        episode_id: str,
        x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
        x_project_id: str | None = Header(default=None, alias="X-Project-ID"),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> EpisodeSummary:
        context, _principal = _authenticated_context(
            settings=effective_auth_settings,
            verifier=oidc_verifier,
            local_resolver=local_resolver,
            tenant_header=x_tenant_id,
            project_header=x_project_id,
            idempotency_header="read-only",
            authorization_header=authorization,
            scope="episodes:read",
        )
        return _summary(
            _scoped_episode(
                application_service,
                tenant_id=context.tenant_id,
                project_id=context.project_id,
                episode_id=episode_id,
            ),
            resource_link_signer=resource_link_signer,
        )

    @app.get("/v1/episodes/{episode_id}/status", response_model=EpisodeStatusResponse)
    def get_status(
        episode_id: str,
        x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
        x_project_id: str | None = Header(default=None, alias="X-Project-ID"),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> EpisodeStatusResponse:
        context, _principal = _authenticated_context(
            settings=effective_auth_settings,
            verifier=oidc_verifier,
            local_resolver=local_resolver,
            tenant_header=x_tenant_id,
            project_header=x_project_id,
            idempotency_header="read-only",
            authorization_header=authorization,
            scope="episodes:read",
        )
        record = _scoped_episode(
            application_service,
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            episode_id=episode_id,
        )
        create_receipt = command_dispatcher.replay(
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            episode_id=record.episode_id,
            command="create",
            idempotency_key=record.idempotency_key,
        )
        return EpisodeStatusResponse(
            episode_id=record.episode_id,
            # The pre-durable repository has one identity for the initial
            # version; the durable version repository will supply distinct IDs.
            episode_version_id=record.episode_id,
            version=record.version,
            stage=_status_stage(record.state),
            progress=_progress(record.state),
            failure=_safe_failure(record.failure)
            if record.failure is not None
            else None,
            workflow_id=(create_receipt.workflow_id if create_receipt else None),
            package_manifest_sha256=record.package_manifest_sha256,
        )

    @app.post("/v1/episodes/{episode_id}/render", status_code=202)
    async def render_episode(
        request: Request,
        episode_id: str,
        x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
        x_project_id: str | None = Header(default=None, alias="X-Project-ID"),
        idempotency_key: str | None = Header(
            default=None,
            alias="Idempotency-Key",
        ),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> CommandReceipt:
        render_request = await _parse_render_body(request)
        render_payload = (
            render_request.model_dump(mode="json", exclude_none=True)
            if render_request is not None
            else None
        )
        context, _principal = _authenticated_context(
            settings=effective_auth_settings,
            verifier=oidc_verifier,
            local_resolver=local_resolver,
            tenant_header=x_tenant_id,
            project_header=x_project_id,
            idempotency_header=idempotency_key,
            authorization_header=authorization,
            scope="episodes:render",
        )
        record = _scoped_episode(
            application_service,
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            episode_id=episode_id,
        )
        replay = command_dispatcher.replay(
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            episode_id=record.episode_id,
            command="render",
            idempotency_key=context.idempotency_key,
            payload=render_payload,
        )
        if replay is not None:
            return replay
        if record.state == EpisodeState.PUBLISHED:
            raise InvalidEpisodeTransition("published episode cannot be rendered")
        command_payload = _workflow_command_payload(
            factory=workflow_snapshot_factory,
            required=require_workflow_snapshot,
            record=record,
            command="render",
            payload=render_payload,
        )
        return command_dispatcher.submit(
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            episode_id=record.episode_id,
            command="render",
            idempotency_key=context.idempotency_key,
            payload=command_payload,
        )

    @app.post("/v1/episodes/{episode_id}/publish", status_code=202)
    async def publish_episode(
        request: Request,
        episode_id: str,
        x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
        x_project_id: str | None = Header(default=None, alias="X-Project-ID"),
        idempotency_key: str | None = Header(
            default=None,
            alias="Idempotency-Key",
        ),
        x_publish_authorization: str | None = Header(
            default=None,
            alias="X-Publish-Authorization",
        ),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> CommandReceipt:
        publish_request = await _parse_publish_body(request)
        context, principal = _authenticated_context(
            settings=effective_auth_settings,
            verifier=oidc_verifier,
            local_resolver=local_resolver,
            tenant_header=x_tenant_id,
            project_header=x_project_id,
            idempotency_header=idempotency_key,
            authorization_header=authorization,
            scope="episodes:publish",
        )
        record = _scoped_episode(
            application_service,
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            episode_id=episode_id,
        )
        publish_payload = (
            _publication_handoff_payload(
                record=record,
                context=context,
                principal=principal,
                request=publish_request,
            )
            if publish_request is not None
            else None
        )
        command_payload = _workflow_command_payload(
            factory=workflow_snapshot_factory,
            required=require_workflow_snapshot,
            record=record,
            command="publish",
            payload=publish_payload,
        )
        replay = command_dispatcher.replay(
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            episode_id=record.episode_id,
            command="publish",
            idempotency_key=context.idempotency_key,
            payload=command_payload,
        )
        if replay is not None:
            return replay
        if effective_auth_settings.mode == "api":
            if publish_request is None:
                raise ApiProblem(
                    code="publish_authorization_required",
                    message="target and scoped approval are required",
                    status=403,
                )
            if approval_repository is None:
                raise ApiProblem(
                    code="approval_store_unavailable",
                    message="publication approval store is unavailable",
                    status=503,
                )
            if record.package_sha256 is None:
                raise ApiProblem(
                    code="package_not_ready",
                    message="a packaged checksum is required before publication",
                    status=409,
                )
            try:
                approval = approval_repository.consume(
                    tenant_id=context.tenant_id,
                    project_id=context.project_id,
                    episode_id=record.episode_id,
                    approval_id=publish_request.approval_id,
                    target_id=publish_request.target_id,
                    operation="publish",
                    package_sha256=record.package_sha256,
                    actor_id=principal.subject,
                    now=datetime.now(UTC),
                )
                if (
                    approval.approval_id != publish_request.approval_id
                    or approval.actor_id != principal.subject
                ):
                    raise ApiProblem(
                        code="publish_authorization_required",
                        message="publication approval is inconsistent",
                        status=403,
                    )
            except ApprovalAlreadyConsumed as error:
                raise ApiProblem(
                    code="publish_authorization_required",
                    message="publication approval is no longer usable",
                    status=403,
                ) from error
            except ApprovalError as error:
                raise ApiProblem(
                    code="publish_authorization_required",
                    message="publication approval is invalid or expired",
                    status=403,
                ) from error
        elif publish_request is not None:
            if approval_repository is None:
                raise ApiProblem(
                    code="approval_store_unavailable",
                    message="publication approval store is unavailable",
                    status=503,
                )
            if record.package_sha256 is None:
                raise ApiProblem(
                    code="package_not_ready",
                    message="a packaged checksum is required before publication",
                    status=409,
                )
            if record.package_manifest_sha256 is None:
                raise ApiProblem(
                    code="package_not_ready",
                    message=(
                        "a package manifest checksum is required before publication"
                    ),
                    status=409,
                )
            try:
                approval = approval_repository.consume(
                    tenant_id=context.tenant_id,
                    project_id=context.project_id,
                    episode_id=record.episode_id,
                    approval_id=publish_request.approval_id,
                    target_id=publish_request.target_id,
                    operation="publish",
                    package_sha256=record.package_sha256,
                    actor_id=principal.subject,
                    now=datetime.now(UTC),
                )
                if (
                    approval.approval_id != publish_request.approval_id
                    or approval.actor_id != principal.subject
                ):
                    raise ApiProblem(
                        code="publish_authorization_required",
                        message="publication approval is inconsistent",
                        status=403,
                    )
            except ApprovalError as error:
                raise ApiProblem(
                    code="publish_authorization_required",
                    message="publication approval is invalid or expired",
                    status=403,
                ) from error
        elif x_publish_authorization != "true":
            raise ApiProblem(
                code="publish_authorization_required",
                message="explicit publish authorization is required",
                status=403,
            )
        if record.state not in {EpisodeState.PACKAGED, EpisodeState.PUBLISHED}:
            raise InvalidEpisodeTransition("episode must be packaged before publish")
        return command_dispatcher.submit(
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            episode_id=record.episode_id,
            command="publish",
            idempotency_key=context.idempotency_key,
            payload=command_payload,
        )

    return app


__all__ = ["create_app"]
