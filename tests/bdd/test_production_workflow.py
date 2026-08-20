"""BDD coverage for complete immutable production orchestration."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest
from pytest_bdd import given, scenarios, then, when
from temporalio.exceptions import ApplicationError

from poddown.audio.contracts import RenderRequest
from poddown.audio.diagnostics import AudioDiagnostics
from poddown.audio.production_workflow import (
    FINAL_MASTER_TRANSCRIPTION_ACTIVITY_NAME,
    MASTER_EPISODE_ACTIVITY_NAME,
    PACKAGE_EPISODE_ACTIVITY_NAME,
    PREPARE_CONTENT_ACTIVITY_NAME,
    PRODUCTION_STAGE_OUTPUT_ERROR_TYPE,
    EpisodeProductionWorkflow,
    ProductionWorkflowInput,
    ProductionWorkflowResult,
)
from poddown.audio.rights import VoiceConsent
from poddown.audio.selection import CandidateQuality
from poddown.audio.workflow import (
    EpisodeWorkflowInput,
    EpisodeWorkflowResult,
    SegmentDecision,
    SegmentWorkflowInput,
)
from poddown.audio.workflow import (
    workflow_id_for as render_workflow_id_for,
)
from poddown.domain import FidelityResult

scenarios("../features/production_workflow.feature")


def _input() -> ProductionWorkflowInput:
    request = RenderRequest(
        "episode-1",
        "version-1",
        "segment-1",
        "host",
        "A deterministic segment.",
        "voice-host-v1",
        "local",
        "local-deterministic-v1",
    )
    render_input = EpisodeWorkflowInput(
        "episode-1",
        "version-1",
        (
            SegmentWorkflowInput(
                "segment-1",
                request,
                VoiceConsent("voice-host-v1", "consent-1", frozenset({"local"})),
                ("deterministic",),
            ),
        ),
    )
    return ProductionWorkflowInput(
        episode_id="episode-1",
        episode_version_id="version-1",
        source_sha256="a" * 64,
        profile_id="profile-1",
        prepared_content_reference={
            "manifest_sha256": "b" * 64,
            "episode_render_workflow_input_json": render_input.to_json(),
            "preparation_activity_input": {
                "tenant_id": "tenant-1",
                "project_id": "project-1",
                "episode_id": "episode-1",
                "source_markdown": "source",
                "profile_id": "profile-1",
            },
        },
        execution_mode="deterministic-local",
        max_cost=Decimal("1.00"),
    )


def _render_result(value: ProductionWorkflowInput) -> str:
    render_input = json.loads(
        value.prepared_content_reference["episode_render_workflow_input_json"]
    )
    return _render_result_from_input(render_input)


def _render_result_from_input(render_input: dict[str, Any]) -> str:
    segment_id = render_input["segments"][0]["segment_id"]
    candidate = CandidateQuality(
        "candidate-1",
        FidelityResult(True, 1.0, "none"),
        AudioDiagnostics(44_100, 1, 1.0, 0.5, 0.0, 0.1),
        True,
        Decimal("0"),
    )
    return EpisodeWorkflowResult(
        render_workflow_id_for(EpisodeWorkflowInput.from_dict(render_input)),
        "completed",
        (SegmentDecision(segment_id, 1, "candidate-1", (candidate,), None),),
        None,
    ).to_json()


def test_source_only_production_input_uses_preparation_render_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _input()
    render_json = original.prepared_content_reference[
        "episode_render_workflow_input_json"
    ]
    references = dict(original.prepared_content_reference)
    del references["episode_render_workflow_input_json"]
    value = replace(original, prepared_content_reference=references)
    calls: list[str] = []

    async def child(_reference: object, payload: str, *, id: str) -> str:
        calls.append("render_segments")
        assert id.endswith("-render")
        return _render_result_from_input(json.loads(render_json))

    async def activity(name: str, *, args: list[dict[str, object]], **_: object):
        calls.append(name)
        if name == "poddown.audio.validate_source":
            return {
                "episode_id": "episode-1",
                "episode_version_id": "version-1",
                "passed": True,
            }
        if name == PREPARE_CONTENT_ACTIVITY_NAME:
            return {
                "episode_id": "episode-1",
                "episode_version_id": "version-1",
                "source_sha256": "a" * 64,
                "resolved_profile_id": "profile-1",
                "resolved_profile_version": "v1",
                "manifest_sha256": "b" * 64,
                "critical_tokens": [{"expected_spoken_form": "deterministic"}],
                "render_workflow_input_json": render_json,
            }
        if name == MASTER_EPISODE_ACTIVITY_NAME:
            return {
                "episode_id": "episode-1",
                "episode_version_id": "version-1",
                "passed": True,
                "master_wav_checksum": "c" * 64,
                "master_mp3_checksum": "d" * 64,
            }
        if name == FINAL_MASTER_TRANSCRIPTION_ACTIVITY_NAME:
            return {
                "episode_id": "episode-1",
                "episode_version_id": "version-1",
                "passed": True,
                "qa_master_checksum": "c" * 64,
                "qa_critical_token_accuracy": 1.0,
            }
        if name == PACKAGE_EPISODE_ACTIVITY_NAME:
            return {
                "episode_id": "episode-1",
                "episode_version_id": "version-1",
                "passed": True,
                "package_sha256": "e" * 64,
                "package_manifest_sha256": "f" * 64,
            }
        raise AssertionError(name)

    monkeypatch.setattr(
        "poddown.audio.production_workflow.workflow.execute_child_workflow", child
    )
    monkeypatch.setattr(
        "poddown.audio.production_workflow.workflow.execute_activity", activity
    )

    result = json.loads(asyncio.run(EpisodeProductionWorkflow().run(value.to_json())))

    assert result["status"] == "completed"
    assert calls == [
        "poddown.audio.validate_source",
        PREPARE_CONTENT_ACTIVITY_NAME,
        "render_segments",
        MASTER_EPISODE_ACTIVITY_NAME,
        FINAL_MASTER_TRANSCRIPTION_ACTIVITY_NAME,
        PACKAGE_EPISODE_ACTIVITY_NAME,
    ]


@given("a valid complete production workflow snapshot")
def valid_snapshot(context: dict[str, Any]) -> None:
    context.values["input"] = _input()
    context.values["calls"] = []
    context.values["qa_accuracy"] = 1.0


@when("the production workflow runs with deterministic stage adapters")
def run_workflow(context: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_adapters(context, monkeypatch)
    context.values["result"] = json.loads(
        asyncio.run(EpisodeProductionWorkflow().run(context.values["input"].to_json()))
    )


@when("the production workflow returns below-perfect final-master accuracy")
def run_bad_qa(context: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    context.values["qa_accuracy"] = 0.5
    _patch_adapters(context, monkeypatch)
    with pytest.raises(ApplicationError) as error:
        asyncio.run(EpisodeProductionWorkflow().run(context.values["input"].to_json()))
    context.values["error"] = error.value


def _patch_adapters(context: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    value = context.values["input"]
    context.values["payloads"] = {}

    async def child(_reference: object, _payload: str, *, id: str) -> str:
        context.values["calls"].append("render_segments")
        assert id.endswith("-render")
        return _render_result(value)

    async def activity(name: str, *, args: list[dict[str, object]], **_: object):
        context.values["calls"].append(name)
        context.values["payloads"][name] = args[0]
        if name == "poddown.audio.validate_source":
            return {
                "episode_id": "episode-1",
                "episode_version_id": "version-1",
                "passed": True,
            }
        if name == PREPARE_CONTENT_ACTIVITY_NAME:
            return {
                "episode_id": "episode-1",
                "episode_version_id": "version-1",
                "source_sha256": "a" * 64,
                "resolved_profile_id": "profile-1",
                "resolved_profile_version": "v1",
                "critical_tokens": [
                    {"occurrence_id": f"tok-{index}"} for index in range(1000)
                ],
                "manifest_sha256": "b" * 64,
            }
        if name == MASTER_EPISODE_ACTIVITY_NAME:
            return {
                "episode_id": "episode-1",
                "episode_version_id": "version-1",
                "passed": True,
                "master_wav_checksum": "c" * 64,
                "master_mp3_checksum": "d" * 64,
            }
        if name == FINAL_MASTER_TRANSCRIPTION_ACTIVITY_NAME:
            return {
                "episode_id": "episode-1",
                "episode_version_id": "version-1",
                "passed": True,
                "qa_master_checksum": "c" * 64,
                "qa_critical_token_accuracy": context.values["qa_accuracy"],
            }
        if name == PACKAGE_EPISODE_ACTIVITY_NAME:
            return {
                "episode_id": "episode-1",
                "episode_version_id": "version-1",
                "passed": True,
                "package_sha256": "e" * 64,
                "package_manifest_sha256": "f" * 64,
            }
        raise AssertionError(name)

    monkeypatch.setattr(
        "poddown.audio.production_workflow.workflow.execute_child_workflow", child
    )
    monkeypatch.setattr(
        "poddown.audio.production_workflow.workflow.execute_activity", activity
    )


@then("it completes with a package and manifest digest")
def assert_complete(context: dict[str, Any]) -> None:
    result = ProductionWorkflowResult(
        workflow_id=context.values["result"]["workflow_id"],
        status=context.values["result"]["status"],
        stage=context.values["result"]["stage"],
        package_sha256=context.values["result"]["package_sha256"],
        package_manifest_sha256=context.values["result"]["package_manifest_sha256"],
    )
    assert result.status == "completed"
    assert result.stage == "packaged"
    assert len(result.package_manifest_sha256) == 64


@then("it invokes the stages in dependency order")
def assert_order(context: dict[str, Any]) -> None:
    assert context.values["calls"] == [
        "poddown.audio.validate_source",
        PREPARE_CONTENT_ACTIVITY_NAME,
        "render_segments",
        MASTER_EPISODE_ACTIVITY_NAME,
        FINAL_MASTER_TRANSCRIPTION_ACTIVITY_NAME,
        PACKAGE_EPISODE_ACTIVITY_NAME,
    ]


@then("it keeps preparation evidence bounded at stage boundaries")
def assert_bounded_preparation_handoffs(context: dict[str, Any]) -> None:
    expected = {
        "source_sha256": "a" * 64,
        "resolved_profile_id": "profile-1",
        "resolved_profile_version": "v1",
        "manifest_sha256": "b" * 64,
    }
    payloads = context.values["payloads"]
    handoff = payloads[MASTER_EPISODE_ACTIVITY_NAME]
    assert handoff["preparation_result"] == expected
    assert "render_input_json" not in handoff
    assert "render_result_json" not in handoff
    assert len(handoff["render_input_sha256"]) == 64
    assert len(handoff["render_result_sha256"]) == 64
    assert "critical_tokens" not in payloads[FINAL_MASTER_TRANSCRIPTION_ACTIVITY_NAME]
    assert payloads[PACKAGE_EPISODE_ACTIVITY_NAME]["preparation"] == expected


@then("the production workflow fails with a non-retryable stage-output error")
def assert_bad_qa(context: dict[str, Any]) -> None:
    error = context.values["error"]
    assert error.type == PRODUCTION_STAGE_OUTPUT_ERROR_TYPE
    assert error.non_retryable is True
