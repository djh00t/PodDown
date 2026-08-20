"""Deterministic, fixture-only source-fidelity evaluation for reasoning models."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from poddown.content.adaptation import (
    AdaptationError,
    AdaptationProposal,
    EpisodeTreatment,
    FixtureReasoningPort,
    adapt_source,
)
from poddown.content.adaptation_envelope import parse_adaptation_envelope
from poddown.content.models import Profile, ScriptTurn, SourceSnapshot
from poddown.content.source import anchor_text
from poddown.content.tokens import extract_critical_tokens

_GATE_NAMES = ("source_anchors", "critical_tokens", "negation", "speaker_dialogue")


@dataclass(frozen=True)
class ReasoningEvalCase:
    """One deterministic source-fidelity corpus case."""

    case_id: str
    source: SourceSnapshot
    profile: Profile
    treatment: EpisodeTreatment

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id:
            raise ValueError("case_id must be a non-empty string")


@dataclass(frozen=True)
class ReasoningModelCandidate:
    """One configured model and its deterministic envelope fixtures."""

    model: str
    configured_cost: Decimal
    envelopes: Mapping[str, Mapping[str, object]]

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not self.model:
            raise ValueError("model must be a non-empty string")
        if (
            not isinstance(self.configured_cost, Decimal)
            or not self.configured_cost.is_finite()
            or self.configured_cost < 0
        ):
            raise ValueError("configured_cost must be a finite non-negative Decimal")
        if not isinstance(self.envelopes, Mapping):
            raise ValueError("envelopes must be a mapping")
        envelopes = {
            case_id: dict(record) for case_id, record in self.envelopes.items()
        }
        if any(
            not isinstance(case_id, str)
            or not case_id
            or not isinstance(record, Mapping)
            for case_id, record in self.envelopes.items()
        ):
            raise ValueError("envelopes must map case IDs to envelope records")
        object.__setattr__(self, "envelopes", MappingProxyType(envelopes))


@dataclass(frozen=True)
class ReasoningEvalCaseResult:
    """Stable gate results for one candidate on one corpus case."""

    case_id: str
    passed: bool
    failure_code: str | None
    gates: Mapping[str, bool]

    def __post_init__(self) -> None:
        gates = dict(self.gates)
        if tuple(gates) != _GATE_NAMES or any(
            type(value) is not bool for value in gates.values()
        ):
            raise ValueError("gates must contain every source-fidelity gate")
        if self.passed != all(gates.values()):
            raise ValueError("passed must match source-fidelity gates")
        if self.passed != (self.failure_code is None):
            raise ValueError("failure_code must match passed")
        object.__setattr__(self, "gates", MappingProxyType(gates))

    def to_record(self) -> dict[str, object]:
        """Return JSON-ready deterministic case evidence."""
        return {
            "case_id": self.case_id,
            "failure_code": self.failure_code,
            "gates": dict(self.gates),
            "passed": self.passed,
        }


@dataclass(frozen=True)
class ReasoningCandidateResult:
    """All corpus outcomes for one configured candidate."""

    model: str
    configured_cost: Decimal
    cases: tuple[ReasoningEvalCaseResult, ...]

    @property
    def passed(self) -> bool:
        """Whether this candidate passed each corpus case."""
        return all(case.passed for case in self.cases)

    def to_record(self) -> dict[str, object]:
        """Return JSON-ready deterministic candidate evidence."""
        return {
            "cases": [case.to_record() for case in self.cases],
            "configured_cost": format(self.configured_cost, "f"),
            "model": self.model,
            "passed": self.passed,
        }


@dataclass(frozen=True)
class ReasoningEvaluationReport:
    """Complete serialized evidence and deterministic selected candidate."""

    candidates: tuple[ReasoningCandidateResult, ...]
    selected_model: str | None

    def to_record(self) -> dict[str, object]:
        """Return a JSON-ready evaluation report without raw source payloads."""
        return {
            "candidates": [candidate.to_record() for candidate in self.candidates],
            "selected_model": self.selected_model,
        }

    def to_json(self) -> str:
        """Serialize the evidence canonically for deterministic persistence."""
        return json.dumps(self.to_record(), separators=(",", ":"), sort_keys=True)


def evaluate_reasoning_candidates(
    cases: Sequence[ReasoningEvalCase], candidates: Sequence[ReasoningModelCandidate]
) -> ReasoningEvaluationReport:
    """Evaluate deterministic envelopes and choose the cheapest passing model.

    This function accepts only preconfigured fixtures. It neither accepts a
    transport nor makes any network request.
    """
    corpus = tuple(cases)
    configured = tuple(candidates)
    _validate_inputs(corpus, configured)
    results = tuple(
        ReasoningCandidateResult(
            candidate.model,
            candidate.configured_cost,
            tuple(_evaluate_case(case, candidate) for case in corpus),
        )
        for candidate in configured
    )
    passing = [candidate for candidate in results if candidate.passed]
    selected = min(
        passing,
        key=lambda candidate: (candidate.configured_cost, candidate.model),
        default=None,
    )
    return ReasoningEvaluationReport(
        results, None if selected is None else selected.model
    )


def _validate_inputs(
    cases: tuple[ReasoningEvalCase, ...],
    candidates: tuple[ReasoningModelCandidate, ...],
) -> None:
    if not cases:
        raise ValueError("cases must not be empty")
    if not candidates:
        raise ValueError("candidates must not be empty")
    case_ids = tuple(case.case_id for case in cases)
    models = tuple(candidate.model for candidate in candidates)
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("case IDs must be unique")
    if len(models) != len(set(models)):
        raise ValueError("candidate models must be unique")
    expected_case_ids = set(case_ids)
    for candidate in candidates:
        if set(candidate.envelopes) != expected_case_ids:
            raise ValueError("candidate envelopes must match corpus case IDs")


def _evaluate_case(
    case: ReasoningEvalCase, candidate: ReasoningModelCandidate
) -> ReasoningEvalCaseResult:
    try:
        envelope = parse_adaptation_envelope(candidate.envelopes[case.case_id])
        if (
            envelope.source_sha256 != case.source.source_sha256
            or envelope.treatment_id != case.treatment.treatment_id
            or envelope.model != candidate.model
        ):
            raise ValueError("fixture envelope identity does not match candidate")
        turns = tuple(
            ScriptTurn(
                turn.turn_id,
                turn.speaker_id,
                turn.text,
                turn.kind,
                turn.source_anchors,
                turn.claim_anchors,
            )
            for turn in envelope.turns
        )
        proposal = AdaptationProposal(case.treatment, turns)
        adapt_source(
            case.source,
            case.profile,
            case.treatment,
            FixtureReasoningPort({case.source.source_sha256: proposal}, {}),
        )
    except AdaptationError as error:
        gates = _gates_for_adaptation_error(
            error, case, turns if "turns" in locals() else ()
        )
        return ReasoningEvalCaseResult(case.case_id, False, error.code, gates)
    except (TypeError, ValueError):
        return ReasoningEvalCaseResult(
            case.case_id,
            False,
            "envelope",
            {name: False for name in _GATE_NAMES},
        )
    return ReasoningEvalCaseResult(
        case.case_id, True, None, {name: True for name in _GATE_NAMES}
    )


def _gates_for_adaptation_error(
    error: AdaptationError, case: ReasoningEvalCase, turns: tuple[ScriptTurn, ...]
) -> dict[str, bool]:
    gates = {name: True for name in _GATE_NAMES}
    if error.code == "missing_anchor":
        gates["source_anchors"] = False
    elif error.code in {"invalid_speaker", "dialogue_quality", "duration"}:
        gates["speaker_dialogue"] = False
    elif error.code == "unsupported_claim":
        gates["negation"] = not _has_negation_mismatch(case, turns)
        if gates["negation"]:
            gates["critical_tokens"] = False
    return gates


def _has_negation_mismatch(
    case: ReasoningEvalCase, turns: tuple[ScriptTurn, ...]
) -> bool:
    expected = 0
    actual = 0
    try:
        for turn in turns:
            actual += _negation_count(turn.text)
            expected += sum(
                _negation_count(anchor_text(case.source, anchor))
                for anchor in turn.claim_anchors
            )
    except ValueError:
        return False
    return expected != actual


def _negation_count(text: str) -> int:
    return sum(token.category == "negation" for token in extract_critical_tokens(text))
