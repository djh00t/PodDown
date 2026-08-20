"""BDD coverage for deterministic reasoning-candidate evaluation."""

from decimal import Decimal
from typing import Any

from pytest_bdd import given, scenarios, then, when

from poddown.content.adaptation import EpisodeTreatment
from poddown.content.models import Profile, SourceAnchor, SpeakerProfile
from poddown.content.source import snapshot_source

scenarios("../features/reasoning_candidate_evals.feature")


def _case_and_envelope(text: str) -> tuple[object, dict[str, object]]:
    from poddown.content.reasoning_evals import ReasoningEvalCase

    source = snapshot_source("# Context\n\nThe controller is not silent.\n")
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
        ("turn-001",),
    )
    envelope: dict[str, object] = {
        "schema_version": "1.0",
        "source_sha256": source.source_sha256,
        "treatment_id": treatment.treatment_id,
        "model": "",
        "request_id": "fixture-001",
        "turns": [
            {
                "turn_id": "turn-001",
                "speaker_id": "host",
                "kind": "factual",
                "text": text,
                "source_anchors": [
                    {"block_id": block.block_id, "start": block.start, "end": block.end}
                ],
                "claim_anchors": [
                    {"block_id": block.block_id, "start": block.start, "end": block.end}
                ],
            }
        ],
        "usage": {"input_tokens": 8, "output_tokens": 5},
        "estimated_cost": "0.00125",
    }
    return ReasoningEvalCase("source-fidelity", source, profile, treatment), envelope


@given("deterministic source-fidelity candidate fixtures")
def deterministic_fixtures(context: Any) -> None:
    from poddown.content.reasoning_evals import ReasoningModelCandidate

    case, envelope = _case_and_envelope("The controller is not silent.")
    context.values["cases"] = (case,)
    context.values["candidates"] = (
        ReasoningModelCandidate(
            "premium",
            Decimal("0.02"),
            {"source-fidelity": {**envelope, "model": "premium"}},
        ),
        ReasoningModelCandidate(
            "economy",
            Decimal("0.01"),
            {"source-fidelity": {**envelope, "model": "economy"}},
        ),
    )


@given("a candidate fixture that changes a source negation")
def negated_fixture(context: Any) -> None:
    from poddown.content.reasoning_evals import ReasoningModelCandidate

    case, envelope = _case_and_envelope("The controller is silent.")
    context.values["cases"] = (case,)
    context.values["candidates"] = (
        ReasoningModelCandidate(
            "broken",
            Decimal("0.01"),
            {"source-fidelity": {**envelope, "model": "broken"}},
        ),
    )


@when("the reasoning candidates are evaluated")
def evaluate_candidates(context: Any) -> None:
    from poddown.content.reasoning_evals import evaluate_reasoning_candidates

    context.values["report"] = evaluate_reasoning_candidates(
        context.values["cases"], context.values["candidates"]
    )


@when("the production reasoning configuration is selected")
def select_production_configuration(context: Any) -> None:
    from poddown.content.reasoning_selection import select_reasoning_model

    context.values["selection"] = select_reasoning_model(
        context.values["cases"], context.values["candidates"]
    )


@then("the cheapest passing candidate is selected with stable evidence")
def cheapest_candidate_is_selected(context: Any) -> None:
    report = context.values["report"]
    assert report.selected_model == "economy"
    assert report.to_json() == report.to_json()


@then("the negation gate fails and no candidate is selected")
def negation_gate_fails(context: Any) -> None:
    report = context.values["report"]
    assert report.selected_model is None
    assert report.candidates[0].cases[0].gates["negation"] is False


@then("the cheapest passing production model has secret-free provenance")
def selected_production_model_has_provenance(context: Any) -> None:
    selection = context.values["selection"]

    assert selection.model == "economy"
    assert selection.configured_cost == Decimal("0.01")
    assert selection.to_record() == {
        "configured_cost": "0.01",
        "evaluation": {
            "candidates": [
                {
                    "cases": [
                        {
                            "case_id": "source-fidelity",
                            "failure_code": None,
                            "gates": {
                                "critical_tokens": True,
                                "negation": True,
                                "source_anchors": True,
                                "speaker_dialogue": True,
                            },
                            "passed": True,
                        }
                    ],
                    "configured_cost": "0.02",
                    "model": "premium",
                    "passed": True,
                },
                {
                    "cases": [
                        {
                            "case_id": "source-fidelity",
                            "failure_code": None,
                            "gates": {
                                "critical_tokens": True,
                                "negation": True,
                                "source_anchors": True,
                                "speaker_dialogue": True,
                            },
                            "passed": True,
                        }
                    ],
                    "configured_cost": "0.01",
                    "model": "economy",
                    "passed": True,
                },
            ],
            "selected_model": "economy",
        },
        "model": "economy",
    }
