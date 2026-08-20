"""Concrete standard-library async HTTP transport for provider adapters."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable, Mapping
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from poddown.providers.http import HttpRequest, HttpResponse


class ProviderTransportError(RuntimeError):
    """A network failure that is safe to classify without exposing response data."""


class _UrlopenResponse(Protocol):
    status: int
    headers: Mapping[str, str]

    def __enter__(self) -> _UrlopenResponse:
        """Enter the response context."""

    def __exit__(self, *_args: object) -> object:
        """Close the response context."""

    def read(self) -> bytes:
        """Read response bytes."""


Urlopen = Callable[..., _UrlopenResponse]


class UrllibAsyncHttpTransport:
    """Run provider HTTP requests through bounded standard-library I/O."""

    def __init__(self, *, opener: Urlopen = urlopen) -> None:
        if not callable(opener):
            raise TypeError("opener must be callable")
        self._opener = opener

    async def request(self, request: HttpRequest) -> HttpResponse:
        """Execute one request without retries or provider-specific policy."""
        if not isinstance(request, HttpRequest):
            raise TypeError("request must be an HttpRequest")
        return await asyncio.to_thread(self._request, request)

    def _request(self, request: HttpRequest) -> HttpResponse:
        body, headers = _encode_body(request)
        outgoing = Request(
            request.url,
            data=body,
            headers=headers,
            method=request.method,
        )
        try:
            response_object = self._opener(
                outgoing,
                timeout=request.timeout_seconds,
            )
            with response_object as response:
                status = getattr(response, "status", None)
                response_headers = getattr(response, "headers", None)
                read = getattr(response, "read", None)
                if type(status) is not int or not isinstance(response_headers, Mapping):
                    raise ProviderTransportError(
                        "provider response metadata is invalid"
                    )
                if not callable(read):
                    raise ProviderTransportError("provider response body is invalid")
                raw_body = read()
                if not isinstance(raw_body, bytes):
                    raise ProviderTransportError("provider response body is invalid")
                return HttpResponse(
                    status=status,
                    headers={
                        str(key): str(value) for key, value in response_headers.items()
                    },
                    body=raw_body,
                )
        except HTTPError as error:
            return HttpResponse(
                status=error.code,
                headers={str(key): str(value) for key, value in error.headers.items()},
                body=error.read(),
            )
        except (TimeoutError, URLError, OSError) as error:
            raise ProviderTransportError("provider network request failed") from error


def _encode_body(request: HttpRequest) -> tuple[bytes | None, dict[str, str]]:
    headers = dict(request.headers)
    if request.json is not None:
        if request.form or request.files:
            raise ValueError("JSON cannot be combined with form or file fields")
        if any(key.casefold() == "content-type" for key in headers):
            return (
                json.dumps(request.json, sort_keys=True, separators=(",", ":")).encode(
                    "utf-8"
                ),
                headers,
            )
        headers["Content-Type"] = "application/json"
        return (
            json.dumps(request.json, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            ),
            headers,
        )
    if request.files:
        boundary = f"poddown-{uuid.uuid4().hex}"
        headers.setdefault("Content-Type", f"multipart/form-data; boundary={boundary}")
        return _multipart_body(request.form, request.files, boundary), headers
    if request.form:
        from urllib.parse import urlencode

        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        return urlencode(request.form).encode("utf-8"), headers
    return None, headers


def _multipart_body(
    form: Mapping[str, str],
    files: Mapping[str, tuple[str, bytes, str]],
    boundary: str,
) -> bytes:
    lines: list[bytes] = []
    marker = boundary.encode("ascii")
    for name, value in form.items():
        lines.extend(
            (
                b"--" + marker,
                f'Content-Disposition: form-data; name="{name}"'.encode(),
                b"",
                value.encode("utf-8"),
            )
        )
    for name, (filename, content, media_type) in files.items():
        if not isinstance(content, bytes):
            raise TypeError("multipart file content must be bytes")
        lines.extend(
            (
                b"--" + marker,
                f'Content-Disposition: form-data; name="{name}"; '
                f'filename="{filename}"'.encode(),
                f"Content-Type: {media_type}".encode("ascii"),
                b"",
                content,
            )
        )
    lines.extend((b"--" + marker + b"--", b""))
    return b"\r\n".join(lines)


__all__ = ["ProviderTransportError", "UrllibAsyncHttpTransport"]
