"""BDD coverage for the OpenAI Responses reasoning transport."""

import asyncio
import json
from typing import Any

from pytest_bdd import given, scenarios, then, when

from poddown.content.adaptation import EpisodeTreatment
from poddown.content.models import Profile, SourceAnchor, SpeakerProfile
from poddown.content.reasoning_request import build_reasoning_request
from poddown.content.source import snapshot_source
from poddown.providers.http import HttpResponse

scenarios("../features/openai_reasoning.feature")


class _Transport:
    def __init__(self) -> None:
        self.request_value: object | None = None

    async def request(self, request: object) -> HttpResponse:
        self.request_value = request
        return HttpResponse(
            200,
            {},
            json.dumps(
                {
                    "id": "resp_001",
                    "model": "gpt-4.1-mini-2025-04-14",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": '{"turns":[]}'},
                            ],
                        }
                    ],
                    "usage": {"input_tokens": 8, "output_tokens": 3},
                }
            ).encode(),
        )


@given("an approved reasoning request and a successful Responses reply")
def approved_request_with_response(context: Any) -> None:
    source = snapshot_source("# Context\n\nThe rate is 5%.\n")
    block = source.blocks[1]
    profile = Profile(
        "profile-001",
        "1.0",
        "narration",
        5,
        (SpeakerProfile("host", "Host", "voice-host"),),
        {},
        {},
        {},
        frozenset(),
    )
    treatment = EpisodeTreatment(
        "treatment-001",
        "narration",
        ("context",),
        5,
        ("context",),
        {"host": "narrator"},
        (SourceAnchor(block.block_id, block.start, block.end),),
    )
    context.values["request"] = build_reasoning_request(source, profile, treatment)
    context.values["transport"] = _Transport()


@when("the OpenAI reasoning transport is called")
def call_reasoning_transport(context: Any) -> None:
    from poddown.content.openai_reasoning import OpenAIResponsesTransport

    context.values["result"] = asyncio.run(
        OpenAIResponsesTransport("gpt-4.1-mini", context.values["transport"]).respond(
            context.values["request"]
        )
    )


@then("the response text and provider metadata are available for adaptation")
def response_metadata_is_available(context: Any) -> None:
    result = context.values["result"]

    assert result.text == '{"turns":[]}'
    assert result.request_id == "resp_001"
    assert result.model == "gpt-4.1-mini-2025-04-14"
    assert result.usage.to_record() == {"input_tokens": 8, "output_tokens": 3}


@then("the dispatched Responses request contains no credentials")
def request_contains_no_credentials(context: Any) -> None:
    request = context.values["transport"].request_value

    assert request.headers == {"Content-Type": "application/json"}
