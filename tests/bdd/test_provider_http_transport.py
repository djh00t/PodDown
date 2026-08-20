"""BDD bindings for the concrete provider HTTP transport."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from pytest_bdd import given, scenarios, then, when

from poddown.providers.http import HttpRequest, HttpResponse
from poddown.providers.http_transport import UrllibAsyncHttpTransport

scenarios("../features/provider_http_transport.feature")


@dataclass
class _Response:
    status: int = 200
    headers: dict[str, str] | None = None
    body: bytes = b"ok"

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class _Opener:
    def __init__(self) -> None:
        self.request = None

    def __call__(self, request, *, timeout: float) -> _Response:
        del timeout
        self.request = request
        return _Response(headers={"x-request-id": "req-1"})


@given("a recording urllib provider opener")
def recording_opener(context: Any) -> None:
    opener = _Opener()
    context.values["opener"] = opener
    context.values["transport"] = UrllibAsyncHttpTransport(opener=opener)


@when("I send a JSON provider request")
def send_json_request(context: Any) -> None:
    context.values["response"] = asyncio.run(
        context.values["transport"].request(
            HttpRequest(
                method="POST",
                url="https://provider.example.test/v1/respond",
                headers={"X-Test": "one"},
                json={"model": "model-1", "input": "hello"},
                timeout_seconds=12,
            )
        )
    )


@then("the opener receives the exact JSON request")
def exact_json_request(context: Any) -> None:
    request = context.values["opener"].request
    assert request.method == "POST"
    assert request.get_header("X-test") == "one"
    assert request.get_header("Content-type") == "application/json"
    assert request.data == b'{"input":"hello","model":"model-1"}'
    assert context.values["response"] == HttpResponse(
        status=200,
        headers={"x-request-id": "req-1"},
        body=b"ok",
    )


@when("I send a multipart provider request")
def send_multipart_request(context: Any) -> None:
    context.values["response"] = asyncio.run(
        context.values["transport"].request(
            HttpRequest(
                method="POST",
                url="https://provider.example.test/v1/transcribe",
                headers={},
                form={"model": "whisper-1"},
                files={"file": ("segment.wav", b"wav-bytes", "audio/wav")},
            )
        )
    )


@then("the opener receives the form and audio parts")
def multipart_request(context: Any) -> None:
    request = context.values["opener"].request
    body = request.data
    content_type = request.get_header("Content-type")
    assert content_type.startswith("multipart/form-data; boundary=")
    assert b'name="model"' in body
    assert b"whisper-1" in body
    assert b'filename="segment.wav"' in body
    assert b"Content-Type: audio/wav" in body
    assert b"wav-bytes" in body
