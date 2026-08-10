"""Thin command-line client for the public PodDown contracts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Protocol
from uuid import UUID

import yaml

from poddown.content.source import snapshot_source
from poddown.intake import validate_markdown

EXIT_OK = 0
EXIT_VALIDATION = 2
EXIT_AUTH = 3
EXIT_WORKFLOW = 4
EXIT_NETWORK = 5
EXIT_INTERRUPTED = 130

_DEFAULT_ENDPOINT = "http://127.0.0.1:8000"
_DEFAULT_PROFILES = frozenset({"default", "technical-dialogue"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TERMINAL_STAGES = frozenset({"failed", "packaged", "published"})


class CliError(RuntimeError):
    """A stable user-facing CLI failure."""

    def __init__(self, message: str, exit_code: int) -> None:
        self.message = message
        self.exit_code = exit_code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class CliConfig:
    """Resolved non-secret CLI configuration."""

    profile: str
    endpoint: str
    output_dir: Path


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """Minimal transport response used by commands and offline tests."""

    status: int
    body: dict[str, object]


class HttpTransport(Protocol):
    """HTTP boundary kept separate from command behavior."""

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> HttpResponse:
        """Send one JSON request and return a decoded JSON response."""


class UrllibTransport:
    """Credential-free standard-library HTTP transport."""

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> HttpResponse:
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
                return HttpResponse(response.status, _decode_json(raw))
        except urllib.error.HTTPError as error:
            raw = error.read().decode("utf-8", errors="replace")
            return HttpResponse(error.code, _decode_json(raw))
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise CliError("network request failed", EXIT_NETWORK) from error


_transport: HttpTransport = UrllibTransport()


def _decode_json(raw: str) -> dict[str, object]:
    try:
        value = json.loads(raw) if raw else {}
    except json.JSONDecodeError as error:
        raise CliError("server returned invalid JSON", EXIT_WORKFLOW) from error
    if not isinstance(value, dict):
        raise CliError("server returned an invalid response", EXIT_WORKFLOW)
    return value


def _read_yaml(path: Path) -> Mapping[str, object]:
    if not path.is_file():
        return {}
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise CliError(
            f"configuration could not be read: {path}", EXIT_VALIDATION
        ) from error
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise CliError(f"configuration must be a mapping: {path}", EXIT_VALIDATION)
    return value


def _frontmatter_config(source: str) -> Mapping[str, object]:
    try:
        snapshot = snapshot_source(source)
    except (TypeError, ValueError):
        return {}
    value = snapshot.frontmatter.get("poddown")
    return value if isinstance(value, Mapping) else {}


def resolve_config(
    source: str,
    *,
    cwd: Path | None = None,
    profile_flag: str | None,
    endpoint_flag: str | None,
    output_dir_flag: str | None,
) -> CliConfig:
    """Resolve flags, frontmatter, project, user, and default configuration."""
    working_dir = Path.cwd() if cwd is None else cwd
    project = _read_yaml(working_dir / ".poddown.yaml")
    user_candidates = (
        Path.home() / ".config" / "poddown" / "config.yaml",
        Path.home() / "config.yaml",
    )
    user: Mapping[str, object] = {}
    for candidate in user_candidates:
        if candidate.is_file():
            user = _read_yaml(candidate)
            break
    frontmatter = _frontmatter_config(source)

    def choose(name: str, flag: str | None, default: str) -> str:
        environment = (
            os.environ.get("PODDOWN_API_ENDPOINT") if name == "endpoint" else None
        )
        for value in (
            flag,
            frontmatter.get(name),
            project.get(name),
            user.get(name),
            environment,
        ):
            if isinstance(value, str) and value.strip():
                return value.strip()
        return default

    profile = choose("profile", profile_flag, "default")
    endpoint = choose("endpoint", endpoint_flag, _DEFAULT_ENDPOINT).rstrip("/")
    output_dir = choose("output_dir", output_dir_flag, "./poddown-output")
    return CliConfig(profile, endpoint, Path(output_dir))


def exit_code_for_http_status(status: int) -> int:
    """Map HTTP outcomes to stable CLI exit categories."""
    if status in {401, 403}:
        return EXIT_AUTH
    if 400 <= status < 500:
        return EXIT_VALIDATION
    if status >= 500:
        return EXIT_WORKFLOW
    return EXIT_OK


def install_verified_package(
    source: Path,
    destination: Path,
    expected_sha256: str,
) -> None:
    """Verify a package and atomically install it without partial output."""
    if _SHA256.fullmatch(expected_sha256) is None:
        raise CliError("expected package checksum is invalid", EXIT_VALIDATION)
    try:
        data = source.read_bytes()
    except OSError as error:
        raise CliError("package could not be read", EXIT_WORKFLOW) from error
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise CliError("package checksum mismatch", EXIT_WORKFLOW)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with NamedTemporaryFile(
            dir=destination.parent,
            prefix=".poddown-package-",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, destination)
    except OSError as error:
        raise CliError("package could not be installed", EXIT_WORKFLOW) from error
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="poddown")
    commands = parser.add_subparsers(dest="command", required=True)

    preview = commands.add_parser("preview")
    preview.add_argument("markdown", type=Path)
    preview.add_argument("--endpoint")
    _add_source_options(preview)

    render = commands.add_parser("render")
    render.add_argument("markdown", type=Path)
    _add_source_options(render, include_json=False)
    _add_http_options(render, wait=True)

    publish = commands.add_parser("publish")
    publish.add_argument("job_id")
    _add_http_options(publish)
    publish.add_argument("--confirm", action="store_true")

    status = commands.add_parser("status")
    status.add_argument("job_id")
    _add_http_options(status, wait=True)

    package_install = commands.add_parser("package-install")
    package_install.add_argument("package", type=Path)
    package_install.add_argument("--output", type=Path, required=True)
    package_install.add_argument("--sha256", required=True)
    package_install.add_argument("--json", action="store_true")
    return parser


def _add_source_options(
    parser: argparse.ArgumentParser,
    *,
    include_json: bool = True,
) -> None:
    parser.add_argument("--profile")
    parser.add_argument("--output-dir")
    if include_json:
        parser.add_argument("--json", action="store_true")


def _add_http_options(parser: argparse.ArgumentParser, *, wait: bool = False) -> None:
    parser.add_argument("--endpoint")
    parser.add_argument("--tenant-id")
    parser.add_argument("--project-id")
    parser.add_argument("--idempotency-key")
    parser.add_argument("--json", action="store_true")
    if wait:
        parser.add_argument("--wait", action="store_true")
        parser.add_argument("--poll-interval", type=float, default=1.0)


def _validate_source(source: str, profile: str) -> tuple[int, str]:
    if profile not in _DEFAULT_PROFILES:
        raise CliError("profile is invalid", EXIT_VALIDATION)
    validation = validate_markdown(
        source,
        _DEFAULT_PROFILES,
        default_profile=profile,
    )
    if not validation.accepted:
        raise CliError("Markdown validation failed", EXIT_VALIDATION)
    source_bytes = source.encode("utf-8")
    return len(source_bytes), hashlib.sha256(source_bytes).hexdigest()


def _emit(value: Mapping[str, object], json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(value, sort_keys=True, separators=(",", ":")))
    else:
        for key, item in value.items():
            print(f"{key}: {item}")


def _read_source(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise CliError("Markdown source could not be read", EXIT_VALIDATION) from error


def _context(args: argparse.Namespace) -> tuple[str, str]:
    tenant = args.tenant_id or os.environ.get("PODDOWN_TENANT_ID")
    project = args.project_id or os.environ.get("PODDOWN_PROJECT_ID")
    try:
        tenant_id = UUID(tenant) if tenant is not None else None
        project_id = UUID(project) if project is not None else None
    except ValueError as error:
        raise CliError("tenant or project context is invalid", EXIT_AUTH) from error
    if tenant_id is None or tenant_id.version != 7:
        raise CliError("tenant context is required", EXIT_AUTH)
    if project_id is None or project_id.version != 7:
        raise CliError("project context is required", EXIT_AUTH)
    return str(tenant_id), str(project_id)


def _request(
    method: str,
    url: str,
    headers: dict[str, str],
    payload: Mapping[str, object] | None = None,
) -> dict[str, object]:
    body = (
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if payload is not None
        else None
    )
    response = _transport.request(method, url, headers, body)
    if not 200 <= response.status < 300:
        raise CliError(
            str(response.body.get("detail", "request failed")),
            exit_code_for_http_status(response.status),
        )
    return response.body


def _wait_for_status(
    endpoint: str,
    job_id: str,
    headers: dict[str, str],
    poll_interval: float,
) -> dict[str, object]:
    while True:
        status = _request("GET", f"{endpoint}/v1/episodes/{job_id}/status", headers)
        stage = status.get("stage")
        if isinstance(stage, str) and stage in _TERMINAL_STAGES:
            return status
        time.sleep(max(0.0, poll_interval))


def _preview(args: argparse.Namespace) -> int:
    source = _read_source(args.markdown)
    config = resolve_config(
        source,
        profile_flag=args.profile,
        endpoint_flag=args.endpoint,
        output_dir_flag=args.output_dir,
    )
    byte_count, digest = _validate_source(source, config.profile)
    _emit(
        {
            "accepted": True,
            "endpoint": config.endpoint,
            "profile": config.profile,
            "source_bytes": byte_count,
            "source_sha256": digest,
        },
        args.json,
    )
    return EXIT_OK


def _render(args: argparse.Namespace) -> int:
    source = _read_source(args.markdown)
    config = resolve_config(
        source,
        profile_flag=args.profile,
        endpoint_flag=args.endpoint,
        output_dir_flag=args.output_dir,
    )
    _validate_source(source, config.profile)
    tenant, project = _context(args)
    key = (
        args.idempotency_key
        or "create-"
        + hashlib.sha256(
            f"{tenant}\x00{project}\x00{config.profile}\x00{source}".encode()
        ).hexdigest()[:16]
    )
    headers = {
        **_auth_headers(),
        "Content-Type": "application/json",
        "X-Tenant-ID": tenant,
        "X-Project-ID": project,
        "Idempotency-Key": key,
    }
    created = _request(
        "POST",
        f"{config.endpoint}/v1/episodes",
        headers,
        {"source": source, "profile": config.profile},
    )
    episode = created.get("episode")
    if not isinstance(episode, Mapping) or not isinstance(episode.get("id"), str):
        raise CliError("API response did not include an episode", EXIT_WORKFLOW)
    episode_id = episode["id"]
    render_headers = {**headers, "Idempotency-Key": f"{key}-render"}
    receipt = _request(
        "POST",
        f"{config.endpoint}/v1/episodes/{episode_id}/render",
        render_headers,
    )
    result: dict[str, object] = {"episode_id": episode_id, **receipt}
    if args.wait:
        result["status"] = _wait_for_status(
            config.endpoint,
            episode_id,
            render_headers,
            args.poll_interval,
        )
    _emit(result, args.json)
    status = result.get("status")
    return (
        EXIT_WORKFLOW
        if isinstance(status, Mapping) and status.get("stage") == "failed"
        else EXIT_OK
    )


def _status(args: argparse.Namespace) -> int:
    config = resolve_config(
        "",
        profile_flag=None,
        endpoint_flag=args.endpoint,
        output_dir_flag=None,
    )
    tenant, project = _context(args)
    headers = {
        **_auth_headers(),
        "X-Tenant-ID": tenant,
        "X-Project-ID": project,
    }
    result = (
        _wait_for_status(config.endpoint, args.job_id, headers, args.poll_interval)
        if args.wait
        else _request(
            "GET",
            f"{config.endpoint}/v1/episodes/{args.job_id}/status",
            headers,
        )
    )
    _emit(result, args.json)
    return EXIT_WORKFLOW if result.get("stage") == "failed" else EXIT_OK


def _publish(args: argparse.Namespace) -> int:
    approved = args.confirm or os.environ.get("PODDOWN_PUBLISH_APPROVED") == "true"
    if not approved:
        raise CliError("explicit publish confirmation is required", EXIT_AUTH)
    config = resolve_config(
        "",
        profile_flag=None,
        endpoint_flag=args.endpoint,
        output_dir_flag=None,
    )
    tenant, project = _context(args)
    headers = {
        **_auth_headers(),
        "X-Tenant-ID": tenant,
        "X-Project-ID": project,
        "Idempotency-Key": args.idempotency_key or f"publish-{args.job_id}",
        "X-Publish-Authorization": "true",
    }
    result = _request(
        "POST",
        f"{config.endpoint}/v1/episodes/{args.job_id}/publish",
        headers,
    )
    _emit(result, args.json)
    return EXIT_OK


def _package_install(args: argparse.Namespace) -> int:
    install_verified_package(args.package, args.output, args.sha256)
    _emit(
        {"installed": str(args.output), "sha256": args.sha256},
        args.json,
    )
    return EXIT_OK


def _auth_headers() -> dict[str, str]:
    token = os.environ.get("PODDOWN_API_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}


def main(argv: Sequence[str] | None = None) -> int:
    """Run one CLI command and return its stable exit code."""
    args = _parser().parse_args(argv)
    try:
        if args.command == "preview":
            return _preview(args)
        if args.command == "render":
            return _render(args)
        if args.command == "status":
            return _status(args)
        if args.command == "publish":
            return _publish(args)
        if args.command == "package-install":
            return _package_install(args)
    except KeyboardInterrupt:
        _emit({"interrupted": True}, bool(getattr(args, "json", False)))
        return EXIT_INTERRUPTED
    except CliError as error:
        _emit(
            {"error": error.message, "exit_code": error.exit_code},
            bool(getattr(args, "json", False)),
        )
        return error.exit_code
    raise CliError("unknown command", EXIT_VALIDATION)


if __name__ == "__main__":
    raise SystemExit(main())
