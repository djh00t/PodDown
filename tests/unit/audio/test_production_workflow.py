"""Contract tests for the episode production workflow skeleton."""

import asyncio
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest
from temporalio.exceptions import ApplicationError

from poddown.audio.contracts import RenderRequest
from poddown.audio.diagnostics import AudioDiagnostics
from poddown.audio.production_workflow import (
    PRODUCTION_POST_RENDER_STAGE_ERROR_TYPE,
    PRODUCTION_RENDER_INPUT_ERROR_TYPE,
    EpisodeProductionWorkflow,
    ProductionStage,
    ProductionWorkflowInput,
    ProductionWorkflowInputError,
    render_workflow_id_for,
    render_workflow_input_for,
    stage_plan_for,
    workflow_id_for,
)
from poddown.audio.rights import VoiceConsent
from poddown.audio.selection import CandidateQuality
from poddown.audio.workflow import (
    EpisodeRenderWorkflow,
    EpisodeWorkflowInput,
    EpisodeWorkflowResult,
    SegmentDecision,
    SegmentWorkflowInput,
)
from poddown.audio.workflow import (
    workflow_id_for as render_result_workflow_id_for,
)
from poddown.domain import FidelityResult


def render_workflow_input(
    *,
    episode_id: str = "episode-1",
    episode_version: str = "version-1",
    provider: str = "local",
) -> EpisodeWorkflowInput:
    """Build the immutable render snapshot prepared for one production run."""
    segment = SegmentWorkflowInput(
        segment_id="segment-1",
        render_request=RenderRequest(
            episode_id=episode_id,
            episode_version=episode_version,
            segment_id="segment-1",
            speaker_id="host",
            expected_spoken_text="A deterministic segment.",
            voice_asset_id="voice-host-v1",
            provider=provider,
            model="local-deterministic-v1",
        ),
        consent=VoiceConsent("voice-host-v1", "consent-1", frozenset({provider})),
        critical_tokens=("deterministic",),
    )
    return EpisodeWorkflowInput(episode_id, episode_version, (segment,))


def production_input(**overrides: object) -> ProductionWorkflowInput:
    """Build a complete immutable input snapshot for one production run."""
    values: dict[str, object] = {
        "episode_id": "episode-1",
        "episode_version_id": "version-1",
        "source_sha256": "a" * 64,
        "profile_id": "profile-1",
        "prepared_content_reference": {
            "manifest_sha256": "b" * 64,
            "script_key": "prepared/episode-1/script.json",
            "episode_render_workflow_input_json": render_workflow_input().to_json(),
        },
        "execution_mode": "deterministic-local",
        "publish_target_id": None,
        "max_cost": Decimal("12.50"),
    }
    values.update(overrides)
    return ProductionWorkflowInput(**values)  # type: ignore[arg-type]


def selected_render_result_json(
    render_input: EpisodeWorkflowInput,
    *,
    result_workflow_id: str | None = None,
    accepted_candidate_id: str | None = "candidate-1",
    candidate_ids: tuple[str, ...] = ("candidate-1",),
) -> str:
    """Build completed render evidence with explicit selected-candidate provenance."""
    candidates = tuple(
        CandidateQuality(
            candidate_id=candidate_id,
            fidelity=FidelityResult(True, 1.0, "none"),
            diagnostics=AudioDiagnostics(44_100, 1, 1.0, 0.5, 0.0, 0.1),
            pronunciation_passed=True,
            soft_score=Decimal("0.80"),
        )
        for candidate_id in candidate_ids
    )
    return EpisodeWorkflowResult(
        workflow_id=result_workflow_id or render_result_workflow_id_for(render_input),
        status="completed",
        decisions=(
            SegmentDecision(
                "segment-1",
                1,
                accepted_candidate_id,
                candidates,
                None,
            ),
        ),
        terminal_failure=None,
    ).to_json()


def test_input_is_frozen_and_round_trips_through_canonical_json():
    """Catch mutable or non-canonical production snapshots crossing Temporal."""
    snapshot = production_input()

    with pytest.raises(FrozenInstanceError):
        snapshot.profile_id = "profile-2"  # type: ignore[misc]
    with pytest.raises(TypeError):
        snapshot.prepared_content_reference["script_key"] = "changed"  # type: ignore[index]

    assert snapshot.to_json() == (
        '{"episode_id":"episode-1","episode_version_id":"version-1",'
        '"execution_mode":"deterministic-local","max_cost":"12.5",'
        '"prepared_content_reference":{"episode_render_workflow_input_json":"'
        '{\\"episode_id\\":\\"episode-1\\",'
        '\\"episode_version\\":\\"version-1\\",\\"max_attempts\\":2,'
        '\\"segments\\":[{\\"consent\\":{\\"allowed_providers\\":[\\"local\\"],'
        '\\"evidence_id\\":\\"consent-1\\",\\"valid\\":true,'
        '\\"voice_asset_id\\":\\"voice-host-v1\\"},'
        '\\"critical_tokens\\":[\\"deterministic\\"],'
        '\\"render_request\\":{\\"attempt\\":1,\\"episode_id\\":\\"episode-1\\",'
        '\\"episode_version\\":\\"version-1\\",'
        '\\"expected_spoken_text\\":\\"A deterministic segment.\\",'
        '\\"model\\":\\"local-deterministic-v1\\",\\"output_format\\":\\"wav\\",'
        '\\"provider\\":\\"local\\",\\"sample_rate_hz\\":44100,'
        '\\"segment_id\\":\\"segment-1\\",\\"speaker_id\\":\\"host\\",'
        '\\"take_index\\":0,\\"voice_asset_id\\":\\"voice-host-v1\\"},'
        '\\"segment_id\\":\\"segment-1\\"}]}","manifest_sha256":"'
        + "b"
        * 64
        + '","script_key":"prepared/episode-1/script.json"},'
        '"profile_id":"profile-1","publish_target_id":null,"source_sha256":"'
        + "a" * 64
        + '"}'
    )
    assert ProductionWorkflowInput.from_json(snapshot.to_json()) == snapshot
    assert snapshot.digest() == production_input().digest()


@pytest.mark.parametrize(
    "override",
    [
        {"episode_id": ""},
        {"episode_version_id": ""},
        {"source_sha256": "not-a-sha256"},
        {"profile_id": ""},
        {"prepared_content_reference": {}},
        {"execution_mode": "local"},
        {"execution_mode": "invalid"},
        {"publish_target_id": ""},
        {"max_cost": Decimal("-0.01")},
    ],
)
def test_input_rejects_invalid_values(override: dict[str, object]):
    """Catch invalid snapshots before they can start a production workflow."""
    with pytest.raises(ProductionWorkflowInputError):
        production_input(**override)


def test_stage_plan_and_identity_are_deterministic_without_publish():
    """Catch an altered stage order or unstable production workflow identity."""
    snapshot = production_input()

    assert stage_plan_for(snapshot) == (
        ProductionStage.VALIDATE_SOURCE,
        ProductionStage.PREPARE_CONTENT,
        ProductionStage.RENDER_SEGMENTS,
        ProductionStage.MASTER,
        ProductionStage.FINAL_MASTER_TRANSCRIPTION,
        ProductionStage.PACKAGE,
    )
    assert workflow_id_for(snapshot) == workflow_id_for(production_input())


@pytest.mark.parametrize(
    ("first_cost", "second_cost"),
    [
        (Decimal("12.50"), Decimal("12.5")),
        (Decimal("0"), Decimal("-0")),
    ],
)
def test_equivalent_decimal_costs_share_canonical_identity(
    first_cost: Decimal, second_cost: Decimal
):
    """Catch equivalent cost values producing different workflow identities."""
    first = production_input(max_cost=first_cost)
    second = production_input(max_cost=second_cost)

    assert first.to_json() == second.to_json()
    assert first.digest() == second.digest()
    assert workflow_id_for(first) == workflow_id_for(second)


@pytest.mark.parametrize(
    ("first_cost", "second_cost"),
    [
        (Decimal("1"), Decimal("10")),
        (Decimal("12"), Decimal("120")),
    ],
)
def test_distinct_integer_decimal_costs_have_distinct_workflow_identities(
    first_cost: Decimal, second_cost: Decimal
):
    """Catch integer trailing zeros being removed from workflow identity."""
    first = production_input(max_cost=first_cost)
    second = production_input(max_cost=second_cost)

    assert first.to_json() != second.to_json()
    assert first.digest() != second.digest()
    assert workflow_id_for(first) != workflow_id_for(second)


@pytest.mark.parametrize(
    ("cost", "canonical_cost"),
    [
        (Decimal("1E+1"), '"10"'),
        (Decimal("1.20E+2"), '"120"'),
        (Decimal("1.250E-2"), '"0.0125"'),
    ],
)
def test_decimal_costs_expand_exponents_without_losing_integer_digits(
    cost: Decimal, canonical_cost: str
):
    """Catch exponent expansion changing a decimal workflow identity."""
    snapshot = production_input(max_cost=cost)

    assert f'"max_cost":{canonical_cost}' in snapshot.to_json()


def test_from_json_rejects_duplicate_object_members():
    """Catch ambiguous JSON payloads being accepted with last-key-wins semantics."""
    duplicate_profile_id = (
        production_input()
        .to_json()
        .replace(
            '"profile_id":"profile-1",',
            '"profile_id":"profile-1","profile_id":"profile-2",',
        )
    )

    with pytest.raises(
        ProductionWorkflowInputError,
        match="^production snapshot is malformed$",
    ):
        ProductionWorkflowInput.from_json(duplicate_profile_id)


def test_stage_plan_includes_publish_only_for_a_publish_target():
    """Catch publish being scheduled without explicit immutable authorization."""
    stages = stage_plan_for(production_input(publish_target_id="rss"))

    assert stages[-1] is ProductionStage.OPTIONAL_PUBLISH


def test_workflow_hands_validated_render_payload_to_deterministic_child(monkeypatch):
    """Catch render payloads or child identities drifting from the production run."""
    snapshot = production_input()
    child_calls: list[tuple[object, str, str]] = []

    async def execute_child_workflow(
        workflow_reference: object, payload: str, *, id: str
    ) -> str:
        child_calls.append((workflow_reference, payload, id))
        return '{"status":"completed"}'

    monkeypatch.setattr(
        "poddown.audio.production_workflow.workflow.execute_child_workflow",
        execute_child_workflow,
    )

    with pytest.raises(ApplicationError) as error:
        asyncio.run(EpisodeProductionWorkflow().run(snapshot.to_json()))

    assert child_calls == [
        (
            EpisodeRenderWorkflow.run,
            render_workflow_input().to_json(),
            render_workflow_id_for(snapshot),
        )
    ]
    assert error.value.type == PRODUCTION_POST_RENDER_STAGE_ERROR_TYPE
    assert error.value.non_retryable is True


def test_workflow_hands_selected_render_result_to_mastering_and_final_qa(monkeypatch):
    """Catch selected candidates bypassing the bound mastering and QA handoff."""
    snapshot = production_input()
    post_render_calls: list[tuple[str, dict[str, object], str]] = []

    async def execute_child_workflow(
        workflow_reference: object, payload: str, *, id: str
    ) -> str:
        del workflow_reference, payload, id
        return selected_render_result_json(render_workflow_input())

    async def execute_activity(
        activity_name: str,
        *,
        args: list[dict[str, object]],
        activity_id: str,
        **kwargs: object,
    ) -> dict[str, object]:
        del kwargs
        post_render_calls.append((activity_name, args[0], activity_id))
        return {
            "episode_id": "episode-1",
            "episode_version_id": "version-1",
            "master_wav_checksum": "c" * 64,
            "master_mp3_checksum": "d" * 64,
            "qa_master_checksum": "c" * 64,
            "qa_passed": True,
            "qa_critical_token_accuracy": 1.0,
        }

    monkeypatch.setattr(
        "poddown.audio.production_workflow.workflow.execute_child_workflow",
        execute_child_workflow,
    )
    monkeypatch.setattr(
        "poddown.audio.production_workflow.workflow.execute_activity",
        execute_activity,
    )

    with pytest.raises(ApplicationError) as error:
        asyncio.run(EpisodeProductionWorkflow().run(snapshot.to_json()))

    assert post_render_calls == [
        (
            "poddown.audio.master_and_final_master_qa",
            {
                "episode_id": "episode-1",
                "episode_version_id": "version-1",
                "render_input_json": render_workflow_input().to_json(),
                "render_result_json": selected_render_result_json(
                    render_workflow_input()
                ),
                "selected_candidate_ids": ["candidate-1"],
            },
            f"{workflow_id_for(snapshot)}-master-final-master-qa",
        )
    ]
    assert error.value.type == PRODUCTION_POST_RENDER_STAGE_ERROR_TYPE


def test_workflow_rejects_render_result_for_a_different_snapshot_before_mastering(
    monkeypatch,
):
    """Catch foreign render result identities reaching mastering dispatch."""
    snapshot = production_input()
    post_render_calls: list[object] = []

    async def execute_child_workflow(
        workflow_reference: object, payload: str, *, id: str
    ) -> str:
        del workflow_reference, payload, id
        return selected_render_result_json(
            render_workflow_input(), result_workflow_id="episode-render-foreign"
        )

    async def execute_activity(activity_name: str, **kwargs: object) -> object:
        post_render_calls.append((activity_name, kwargs))
        return {}

    monkeypatch.setattr(
        "poddown.audio.production_workflow.workflow.execute_child_workflow",
        execute_child_workflow,
    )
    monkeypatch.setattr(
        "poddown.audio.production_workflow.workflow.execute_activity", execute_activity
    )

    with pytest.raises(ApplicationError) as error:
        asyncio.run(EpisodeProductionWorkflow().run(snapshot.to_json()))

    assert error.value.type == PRODUCTION_POST_RENDER_STAGE_ERROR_TYPE
    assert error.value.non_retryable is True
    assert post_render_calls == []


@pytest.mark.parametrize(
    ("accepted_candidate_id", "candidate_ids"),
    [
        (None, ("candidate-1",)),
        ("candidate-1", ()),
        ("candidate-1", ("candidate-2",)),
    ],
)
def test_workflow_rejects_unproven_selected_candidate_before_mastering(
    monkeypatch,
    accepted_candidate_id: str | None,
    candidate_ids: tuple[str, ...],
):
    """Catch missing or mismatched selected-candidate quality evidence."""
    snapshot = production_input()
    post_render_calls: list[object] = []

    async def execute_child_workflow(
        workflow_reference: object, payload: str, *, id: str
    ) -> str:
        del workflow_reference, payload, id
        return selected_render_result_json(
            render_workflow_input(),
            accepted_candidate_id=accepted_candidate_id,
            candidate_ids=candidate_ids,
        )

    async def execute_activity(activity_name: str, **kwargs: object) -> object:
        post_render_calls.append((activity_name, kwargs))
        return {}

    monkeypatch.setattr(
        "poddown.audio.production_workflow.workflow.execute_child_workflow",
        execute_child_workflow,
    )
    monkeypatch.setattr(
        "poddown.audio.production_workflow.workflow.execute_activity", execute_activity
    )

    with pytest.raises(ApplicationError) as error:
        asyncio.run(EpisodeProductionWorkflow().run(snapshot.to_json()))

    assert error.value.type == PRODUCTION_POST_RENDER_STAGE_ERROR_TYPE
    assert error.value.non_retryable is True
    assert post_render_calls == []


def test_workflow_rejects_malformed_mastering_and_final_qa_evidence(monkeypatch):
    """Catch a later-stage adapter falsely completing with unbound evidence."""
    snapshot = production_input()
    post_render_calls: list[dict[str, object]] = []

    async def execute_child_workflow(
        workflow_reference: object, payload: str, *, id: str
    ) -> str:
        del workflow_reference, payload, id
        return selected_render_result_json(render_workflow_input())

    async def execute_activity(
        activity_name: str,
        *,
        args: list[dict[str, object]],
        activity_id: str,
        **kwargs: object,
    ) -> dict[str, object]:
        del activity_name, activity_id, kwargs
        post_render_calls.append(args[0])
        return {"qa_passed": True}

    monkeypatch.setattr(
        "poddown.audio.production_workflow.workflow.execute_child_workflow",
        execute_child_workflow,
    )
    monkeypatch.setattr(
        "poddown.audio.production_workflow.workflow.execute_activity",
        execute_activity,
    )

    with pytest.raises(ApplicationError) as error:
        asyncio.run(EpisodeProductionWorkflow().run(snapshot.to_json()))

    assert post_render_calls
    assert error.value.type == "ProductionPostRenderOutputRejected"
    assert error.value.non_retryable is True


@pytest.mark.parametrize(
    "reference",
    [
        {"manifest_sha256": "b" * 64},
        {
            "manifest_sha256": "b" * 64,
            "episode_render_workflow_input_json": "{",
        },
        {
            "manifest_sha256": "b" * 64,
            "episode_render_workflow_input_json": render_workflow_input(
                episode_id="other-episode"
            ).to_json(),
        },
    ],
)
def test_workflow_rejects_missing_malformed_or_mismatched_render_reference(
    reference: dict[str, object],
):
    """Catch unsafe preparation references before a render child can start."""
    with pytest.raises(ApplicationError) as error:
        asyncio.run(
            EpisodeProductionWorkflow().run(
                production_input(prepared_content_reference=reference).to_json()
            )
        )

    assert error.value.type == PRODUCTION_RENDER_INPUT_ERROR_TYPE
    assert error.value.non_retryable is True


def test_render_adapter_returns_only_the_matching_immutable_episode_snapshot():
    """Catch the adapter accepting a render snapshot for a different episode version."""
    snapshot = production_input()

    assert render_workflow_input_for(snapshot) == render_workflow_input()
    assert render_workflow_id_for(snapshot) == f"{workflow_id_for(snapshot)}-render"


def test_render_adapter_rejects_a_provider_not_authorized_for_execution_mode():
    """Catch deterministic production snapshots dispatching live renderers."""
    snapshot = production_input(
        prepared_content_reference={
            "manifest_sha256": "b" * 64,
            "episode_render_workflow_input_json": render_workflow_input(
                provider="elevenlabs"
            ).to_json(),
        }
    )

    with pytest.raises(ProductionWorkflowInputError):
        render_workflow_input_for(snapshot)


def test_render_adapter_rejects_duplicate_members_in_embedded_render_json():
    """Catch nested render JSON members being silently overwritten at dispatch."""
    payload = (
        render_workflow_input()
        .to_json()
        .replace(
            '"provider":"local"',
            '"provider":"local","provider":"local"',
            1,
        )
    )
    snapshot = production_input(
        prepared_content_reference={
            "manifest_sha256": "b" * 64,
            "episode_render_workflow_input_json": payload,
        }
    )

    with pytest.raises(ProductionWorkflowInputError):
        render_workflow_input_for(snapshot)
