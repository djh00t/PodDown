"""BDD acceptance tests for live reasoning in the preparation pipeline."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pytest_bdd import given, scenarios, then, when

from poddown.content.models import ScriptTurn, ScriptVersion
from poddown.content.service import (
    ContentPreparationRequest,
    prepare_content_live,
)

scenarios("../features/live_preparation.feature")


SOURCE = "LiDAR improves accuracy.\n"
PROFILE = """
profile_id: live-preparation
version: v1
format_type: narration
target_minutes: 1
speakers:
  - speaker_id: host
    display_name: Host
    voice_asset_id: voice-host
"""


class RecordingAdapter:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    async def adapt(self, source, profile, treatment) -> ScriptVersion:
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider adaptation failed")
        anchor = treatment.source_anchors[0]
        return ScriptVersion(
            script_id="provider-script",
            source_sha256=source.source_sha256,
            profile_id=profile.profile_id,
            turns=(
                ScriptTurn(
                    "turn-1",
                    "host",
                    "LiDAR improves accuracy.",
                    "factual",
                    (anchor,),
                    (anchor,),
                ),
            ),
            canonical_hash="a" * 64,
        )


def _request() -> ContentPreparationRequest:
    from poddown.content.adaptation import EpisodeTreatment, FixtureReasoningPort
    from poddown.content.lexicon import (
        PronunciationEntry,
        PronunciationLexicon,
    )
    from poddown.content.models import SourceAnchor, VoiceAsset, VoiceConsent
    from poddown.content.segmentation import SegmentationCapabilities
    from poddown.content.source import snapshot_source

    snapshot = snapshot_source(SOURCE)
    block = snapshot.blocks[0]
    anchor = SourceAnchor(block.block_id, block.start, block.end)
    treatment = EpisodeTreatment(
        "live-treatment",
        "narration",
        ("source-bound",),
        1,
        ("reference",),
        {"host": "presenter"},
        (anchor,),
        ("turn-1",),
    )
    return ContentPreparationRequest(
        markdown=SOURCE,
        profile_yaml=PROFILE,
        treatment=treatment,
        reasoning=FixtureReasoningPort({}, {}),
        lexicon_layers={
            "episode": PronunciationLexicon(
                "episode",
                "live-preparation-v1",
                (
                    PronunciationEntry(
                        "lidar",
                        "LiDAR",
                        "LIE-dar",
                        "live-preparation-v1",
                        "technical_term",
                    ),
                ),
            )
        },
        capabilities=SegmentationCapabilities(100, None, frozenset({"host"})),
        voice_assets=(VoiceAsset("voice-host", True),),
        consents=(VoiceConsent("voice-host", True),),
    )


@given("a source-bound live preparation request and recording adapter")
def live_request(context: Any) -> None:
    context.values["request"] = _request()
    context.values["adapter"] = RecordingAdapter()


@given("a source-bound live preparation request and failing adapter")
def failing_live_request(context: Any) -> None:
    context.values["request"] = _request()
    context.values["adapter"] = RecordingAdapter(fail=True)


@when("I prepare content through the live adapter")
def prepare_live(context: Any) -> None:
    context.values["result"] = asyncio.run(
        prepare_content_live(context.values["request"], context.values["adapter"])
    )


@when("live preparation is attempted")
def attempt_live(context: Any) -> None:
    with pytest.raises(RuntimeError, match="provider adaptation failed") as error:
        asyncio.run(
            prepare_content_live(context.values["request"], context.values["adapter"])
        )
    context.values["error"] = error.value


@then("the live adapter was called once")
def adapter_called_once(context: Any) -> None:
    assert context.values["adapter"].calls == 1


@then("the prepared manifest records one provider call")
def provider_call_recorded(context: Any) -> None:
    assert context.values["result"].manifest["provider_calls"] == 1


@then("the prepared script remains source-bound")
def script_is_source_bound(context: Any) -> None:
    result = context.values["result"]
    assert result.script.source_sha256 == result.snapshot.source_sha256
    assert result.tokens[0].source_span[0] >= 0


@then("live preparation fails without a fixture fallback")
def no_fixture_fallback(context: Any) -> None:
    assert str(context.values["error"]) == "provider adaptation failed"
    assert context.values["adapter"].calls == 1
