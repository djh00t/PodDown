"""BDD acceptance tests for the explicit Transistor HTTP boundary."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

from pytest_bdd import given, scenarios, then, when

from poddown.artifacts import FilesystemArtifactStore
from poddown.packages import EpisodePackage, PackageProvenance
from poddown.publishing import (
    DisclosurePolicy,
    PublicationHttpRequest,
    PublicationHttpResponse,
    PublicationTarget,
    PublicationUncertainOutcomeError,
    TransistorPublicationAdapter,
)

scenarios("../features/transistor_http.feature")

TENANT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")


class RecordingTransport:
    def __init__(self, *, uncertain: bool = False) -> None:
        self.requests: list[PublicationHttpRequest] = []
        self.uncertain = uncertain

    def request(self, request: PublicationHttpRequest) -> PublicationHttpResponse:
        self.requests.append(request)
        if self.uncertain and request.method == "POST":
            raise TimeoutError("provider response was not received")
        if request.method == "GET":
            return PublicationHttpResponse(
                200,
                {},
                json.dumps(
                    {
                        "data": {
                            "attributes": {
                                "upload_url": "https://uploads.example.test/put",
                                "audio_url": "https://media.example.test/episode.mp3",
                                "content_type": "audio/mpeg",
                            }
                        }
                    }
                ).encode(),
            )
        if request.method == "PUT":
            return PublicationHttpResponse(200, {}, b"")
        return PublicationHttpResponse(
            201,
            {},
            b'{"data":{"id":"transistor-episode-2","type":"episode"}}',
        )


def _target() -> PublicationTarget:
    return PublicationTarget(
        TENANT,
        PROJECT,
        "transistor-target",
        "transistor",
        "secret://transistor/api-key",
        "show-1",
        "https://feeds.example.test/show.xml",
        DisclosurePolicy(),
    )


def _package(tmp_path: Path) -> EpisodePackage:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    audio = b"episode audio"
    reference = store.put("episode.mp3", "audio/mpeg", audio)
    return EpisodePackage(
        "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11",
        (reference,),
        PackageProvenance(
            "b" * 64,
            1,
            "v1",
            "elevenlabs",
            "pass",
            1.0,
            sha256(audio).hexdigest(),
        ),
    )


@given("a QA-passed package and a recording Transistor transport")
def configured_context(context: Any, tmp_path: Path) -> None:
    transport = RecordingTransport()
    context.values.update(
        package=_package(tmp_path),
        target=_target(),
        transport=transport,
        adapter=TransistorPublicationAdapter(
            "https://api.example.test/v1",
            secret_resolver=lambda reference: (
                "test-header-value"
                if reference == "secret://transistor/api-key"
                else ""
            ),
            transport=transport,
        ),
    )


@given("a QA-passed package and an uncertain Transistor transport")
def uncertain_context(context: Any, tmp_path: Path) -> None:
    transport = RecordingTransport(uncertain=True)
    context.values.update(
        package=_package(tmp_path),
        target=_target(),
        transport=transport,
        adapter=TransistorPublicationAdapter(
            "https://api.example.test/v1",
            secret_resolver=lambda _reference: "test-header-value",
            transport=transport,
        ),
    )


@when("I publish through the guarded Transistor adapter")
def publish(context: Any) -> None:
    try:
        context.values["external_id"] = context.values["adapter"].publish(
            context.values["package"],
            context.values["target"],
            {"episode.mp3": b"episode audio"},
        )
    except PublicationUncertainOutcomeError as error:
        context.values["error"] = error


@then("the adapter performs authorization upload and create in order")
def request_sequence(context: Any) -> None:
    requests = context.values["transport"].requests
    assert [request.method for request in requests] == ["GET", "PUT", "POST"]
    assert requests[0].url.endswith("/episodes/authorize_upload?filename=episode.mp3")
    assert requests[1].url == "https://uploads.example.test/put"
    assert requests[2].form["episode[show_id]"] == "show-1"
    assert context.values["external_id"] == "transistor-episode-2"


@then("the provider credential is sent only as a request header")
def credential_header(context: Any) -> None:
    requests = context.values["transport"].requests
    assert requests[0].headers["x-api-key"] == "test-header-value"
    assert "test-header-value" not in repr(context.values["target"])
    assert "test-header-value" not in repr(context.values["adapter"])


@then("the publication outcome is marked uncertain")
def uncertain_outcome(context: Any) -> None:
    assert isinstance(context.values["error"], PublicationUncertainOutcomeError)
