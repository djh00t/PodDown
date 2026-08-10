"""Offline FastAPI transport for the Episode Platform contract."""

from __future__ import annotations

import json
from collections.abc import Callable, Collection, Mapping
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from poddown.api.models import (
    CommandReceipt,
    EpisodeCreateRequest,
    EpisodeCreateResponse,
    EpisodeStatusResponse,
    EpisodeSummary,
    ProblemDetail,
    RequestContext,
)
from poddown.api.runtime import CommandDispatcher, InMemoryCommandDispatcher
from poddown.episode_service import (
    EpisodeApplicationService,
    EpisodeCreateCommand,
    EpisodeRecord,
    EpisodeServiceError,
    EpisodeState,
    InMemoryEpisodeRepository,
    InvalidEpisodeTransition,
    StructuredFailure,
)
from poddown.persistence import SQLiteCommandDispatcher, SQLiteEpisodeRepository
from poddown.production_readiness import DependencyState, HealthEvaluator

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


def _summary(record: EpisodeRecord) -> EpisodeSummary:
    """Map the internal record to a source-safe API summary."""
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


def _safe_failure(failure: StructuredFailure) -> dict[str, object]:
    """Allowlist failure fields safe for polling clients."""
    return {
        "code": failure.code,
        "stage": failure.stage,
        "status": failure.status,
        "retriable": failure.retriable,
    }


def create_app(
    service: EpisodeApplicationService | None = None,
    *,
    dispatcher: CommandDispatcher | None = None,
    available_profiles: Collection[str] | None = None,
    database_path: Path | str | None = None,
    health_dependencies: Mapping[str, DependencyState] | None = None,
    health_probes: Mapping[str, Callable[[], bool]] | None = None,
) -> FastAPI:
    """Create an offline or restart-safe app with injected lifecycle ports."""
    if service is not None and database_path is not None:
        raise ValueError("service and database_path are mutually exclusive")
    profiles = frozenset(
        _DEFAULT_PROFILES if available_profiles is None else available_profiles
    )
    if service is not None:
        application_service = service
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
    command_dispatcher = (
        dispatcher
        if dispatcher is not None
        else (
            SQLiteCommandDispatcher(database_path)
            if database_path is not None
            else InMemoryCommandDispatcher()
        )
    )
    app = FastAPI(
        title="PodDown Episode API", version="v1", docs_url=None, redoc_url=None
    )
    health_evaluator = HealthEvaluator()
    dependency_states = {} if health_dependencies is None else dict(health_dependencies)
    dependency_probes = {} if health_probes is None else dict(health_probes)

    @app.get("/health/live")
    def health_live() -> dict[str, object]:
        return {"status": "healthy"}

    @app.get("/health/ready")
    def health_ready() -> dict[str, object]:
        snapshot = health_evaluator.evaluate(
            liveness=True, dependencies=dependency_states, probes=dependency_probes
        )
        return {"status": snapshot.readiness, **snapshot.to_dict()}

    @app.get("/health/dependencies")
    def health_dependency_status() -> dict[str, object]:
        return health_evaluator.evaluate(
            liveness=True, dependencies=dependency_states, probes=dependency_probes
        ).to_dict()

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
    ) -> EpisodeCreateResponse:
        context = _parse_context(
            tenant_header=x_tenant_id,
            project_header=x_project_id,
            idempotency_header=idempotency_key,
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
        receipt = command_dispatcher.submit(
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            episode_id=record.episode_id,
            command="create",
            idempotency_key=context.idempotency_key,
        )
        return EpisodeCreateResponse(episode=_summary(record), receipt=receipt)

    @app.get("/v1/episodes/{episode_id}", response_model=EpisodeSummary)
    def get_episode(
        episode_id: str,
        x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
        x_project_id: str | None = Header(default=None, alias="X-Project-ID"),
    ) -> EpisodeSummary:
        context = _parse_context(
            tenant_header=x_tenant_id,
            project_header=x_project_id,
            idempotency_header="read-only",
        )
        return _summary(
            _scoped_episode(
                application_service,
                tenant_id=context.tenant_id,
                project_id=context.project_id,
                episode_id=episode_id,
            )
        )

    @app.get("/v1/episodes/{episode_id}/status", response_model=EpisodeStatusResponse)
    def get_status(
        episode_id: str,
        x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
        x_project_id: str | None = Header(default=None, alias="X-Project-ID"),
    ) -> EpisodeStatusResponse:
        context = _parse_context(
            tenant_header=x_tenant_id,
            project_header=x_project_id,
            idempotency_header="read-only",
        )
        record = _scoped_episode(
            application_service,
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            episode_id=episode_id,
        )
        return EpisodeStatusResponse(
            episode_id=record.episode_id,
            version=record.version,
            stage=record.state.value,
            progress=_progress(record.state),
            failure=_safe_failure(record.failure)
            if record.failure is not None
            else None,
        )

    @app.post("/v1/episodes/{episode_id}/render", status_code=202)
    def render_episode(
        episode_id: str,
        x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
        x_project_id: str | None = Header(default=None, alias="X-Project-ID"),
        idempotency_key: str | None = Header(
            default=None,
            alias="Idempotency-Key",
        ),
    ) -> CommandReceipt:
        context = _parse_context(
            tenant_header=x_tenant_id,
            project_header=x_project_id,
            idempotency_header=idempotency_key,
        )
        record = _scoped_episode(
            application_service,
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            episode_id=episode_id,
        )
        replay = command_dispatcher.replay(
            tenant_id=context.tenant_id,
            episode_id=record.episode_id,
            command="render",
            idempotency_key=context.idempotency_key,
        )
        if replay is not None:
            return replay
        if record.state == EpisodeState.PUBLISHED:
            raise InvalidEpisodeTransition("published episode cannot be rendered")
        return command_dispatcher.submit(
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            episode_id=record.episode_id,
            command="render",
            idempotency_key=context.idempotency_key,
        )

    @app.post("/v1/episodes/{episode_id}/publish", status_code=202)
    def publish_episode(
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
    ) -> CommandReceipt:
        context = _parse_context(
            tenant_header=x_tenant_id,
            project_header=x_project_id,
            idempotency_header=idempotency_key,
        )
        record = _scoped_episode(
            application_service,
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            episode_id=episode_id,
        )
        if x_publish_authorization != "true":
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
        )

    return app


__all__ = ["create_app"]
