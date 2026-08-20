"""Focused unit coverage for deterministic reasoning-candidate evaluation."""

from decimal import Decimal

import pytest

from poddown.content.adaptation import EpisodeTreatment
from poddown.content.models import Profile, SourceAnchor, SpeakerProfile
from poddown.content.reasoning_evals import (
    ReasoningEvalCase,
    ReasoningModelCandidate,
    evaluate_reasoning_candidates,
)
from poddown.content.source import snapshot_source


def _case_and_envelope() -> tuple[ReasoningEvalCase, dict[str, object]]:
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
                "text": "The controller is not silent.",
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


def test_selects_lexical_model_id_for_equal_cost_passing_candidates() -> None:
    """Changing equal-cost ordering cannot change the deterministic winner."""
    case, envelope = _case_and_envelope()
    candidates = tuple(
        ReasoningModelCandidate(
            model, Decimal("0.01"), {case.case_id: {**envelope, "model": model}}
        )
        for model in ("zeta", "alpha")
    )

    report = evaluate_reasoning_candidates((case,), candidates)

    assert report.selected_model == "alpha"
    assert report.to_json() == (
        '{"candidates":[{"cases":[{"case_id":"source-fidelity",'
        '"failure_code":null,"gates":{"critical_tokens":true,"negation":true,'
        '"source_anchors":true,"speaker_dialogue":true},"passed":true}],'
        '"configured_cost":"0.01","model":"zeta","passed":true},'
        '{"cases":[{"case_id":"source-fidelity","failure_code":null,'
        '"gates":{"critical_tokens":true,"negation":true,"source_anchors":true,'
        '"speaker_dialogue":true},"passed":true}],"configured_cost":"0.01",'
        '"model":"alpha","passed":true}],"selected_model":"alpha"}'
    )


def test_rejects_candidate_envelopes_that_do_not_cover_the_corpus() -> None:
    """A missing fixture cannot accidentally qualify a configured model."""
    case, _ = _case_and_envelope()
    candidate = ReasoningModelCandidate("alpha", Decimal("0.01"), {})

    with pytest.raises(ValueError, match="corpus case IDs"):
        evaluate_reasoning_candidates((case,), (candidate,))


def test_selection_returns_the_cheapest_passing_model_with_evidence() -> None:
    """Production composition gets only model, cost, and deterministic evidence."""
    from poddown.content.reasoning_selection import (
        parse_reasoning_model_selection_record,
        select_reasoning_model,
    )

    case, envelope = _case_and_envelope()
    selection = select_reasoning_model(
        (case,),
        (
            ReasoningModelCandidate(
                "premium",
                Decimal("0.02"),
                {case.case_id: {**envelope, "model": "premium"}},
            ),
            ReasoningModelCandidate(
                "economy",
                Decimal("0.01"),
                {case.case_id: {**envelope, "model": "economy"}},
            ),
        ),
    )

    assert selection.model == "economy"
    assert selection.configured_cost == Decimal("0.01")
    assert selection.evaluation.selected_model == "economy"
    assert selection.to_record()["evaluation"] == selection.evaluation.to_record()
    parsed = parse_reasoning_model_selection_record(selection.to_record())
    assert parsed == selection


def test_selection_record_rejects_inconsistent_evaluation() -> None:
    """Runtime composition cannot trust a record whose selected model failed."""
    from poddown.content.reasoning_selection import (
        parse_reasoning_model_selection_record,
        select_reasoning_model,
    )

    case, envelope = _case_and_envelope()
    selection = select_reasoning_model(
        (case,),
        (
            ReasoningModelCandidate(
                "economy",
                Decimal("0.01"),
                {case.case_id: {**envelope, "model": "economy"}},
            ),
        ),
    )
    record = selection.to_record()
    evaluation = record["evaluation"]
    assert isinstance(evaluation, dict)
    evaluation["selected_model"] = "other"

    with pytest.raises(ValueError, match="selection must match"):
        parse_reasoning_model_selection_record(record)


def test_selection_fails_closed_when_no_candidate_passes() -> None:
    """A source-fidelity failure cannot fall back to an unevaluated model."""
    from poddown.content.reasoning_selection import (
        ReasoningSelectionError,
        select_reasoning_model,
    )

    case, envelope = _case_and_envelope()

    with pytest.raises(ReasoningSelectionError, match="reasoning selection rejected"):
        select_reasoning_model(
            (case,),
            (
                ReasoningModelCandidate(
                    "broken",
                    Decimal("0.01"),
                    {case.case_id: {**envelope, "model": "broken", "turns": []}},
                ),
            ),
        )


def test_selection_fails_closed_for_incomplete_configuration() -> None:
    """Production callers cannot bypass the evaluator's corpus coverage checks."""
    from poddown.content.reasoning_selection import (
        ReasoningSelectionError,
        select_reasoning_model,
    )

    case, _ = _case_and_envelope()

    with pytest.raises(ReasoningSelectionError, match="reasoning selection rejected"):
        select_reasoning_model(
            (case,),
            (ReasoningModelCandidate("economy", Decimal("0.01"), {}),),
        )
