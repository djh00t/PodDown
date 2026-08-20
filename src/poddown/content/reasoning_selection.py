"""Offline selection of a source-faithful reasoning model configuration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from poddown.content.reasoning_evals import (
    ReasoningCandidateResult,
    ReasoningEvalCase,
    ReasoningEvalCaseResult,
    ReasoningEvaluationReport,
    ReasoningModelCandidate,
    evaluate_reasoning_candidates,
)


class ReasoningSelectionError(ValueError):
    """A stable rejection for incomplete or source-unfaithful configuration."""

    def __init__(self) -> None:
        super().__init__("reasoning selection rejected")


@dataclass(frozen=True)
class ReasoningModelSelection:
    """Selected model and secret-free deterministic evaluation provenance."""

    model: str
    configured_cost: Decimal
    evaluation: ReasoningEvaluationReport

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not self.model:
            raise ValueError("model must be a non-empty string")
        if (
            not isinstance(self.configured_cost, Decimal)
            or not self.configured_cost.is_finite()
            or self.configured_cost < 0
        ):
            raise ValueError("configured_cost must be a finite non-negative Decimal")
        if not isinstance(self.evaluation, ReasoningEvaluationReport):
            raise TypeError("evaluation must be a ReasoningEvaluationReport")
        candidate = next(
            (
                result
                for result in self.evaluation.candidates
                if result.model == self.model
            ),
            None,
        )
        if (
            self.evaluation.selected_model != self.model
            or candidate is None
            or not candidate.passed
            or candidate.configured_cost != self.configured_cost
        ):
            raise ValueError("selection must match a passing evaluated candidate")

    def to_record(self) -> dict[str, object]:
        """Return model and evaluation provenance without source or credentials."""
        return {
            "configured_cost": format(self.configured_cost, "f"),
            "evaluation": self.evaluation.to_record(),
            "model": self.model,
        }


def parse_reasoning_model_selection_record(
    value: Mapping[str, object],
) -> ReasoningModelSelection:
    """Parse secret-free R09 selection provenance for runtime composition."""
    if not isinstance(value, Mapping):
        raise ValueError("selection record must be an object")
    model = value.get("model")
    configured_cost = _decimal_value(value.get("configured_cost"))
    evaluation_value = value.get("evaluation")
    if not isinstance(model, str) or not model:
        raise ValueError("selection model is invalid")
    if not isinstance(evaluation_value, Mapping):
        raise ValueError("selection evaluation is invalid")
    evaluation = _evaluation_from_record(evaluation_value)
    return ReasoningModelSelection(model, configured_cost, evaluation)


def _evaluation_from_record(
    value: Mapping[str, object],
) -> ReasoningEvaluationReport:
    candidates_value = value.get("candidates")
    selected_model = value.get("selected_model")
    if not isinstance(candidates_value, list):
        raise ValueError("selection candidates are invalid")
    if selected_model is not None and (
        not isinstance(selected_model, str) or not selected_model
    ):
        raise ValueError("selected reasoning model is invalid")
    candidates: list[ReasoningCandidateResult] = []
    for candidate_value in candidates_value:
        if not isinstance(candidate_value, Mapping):
            raise ValueError("selection candidate is invalid")
        model = candidate_value.get("model")
        cases_value = candidate_value.get("cases")
        passed = candidate_value.get("passed")
        if (
            not isinstance(model, str)
            or not model
            or not isinstance(cases_value, list)
            or type(passed) is not bool
        ):
            raise ValueError("selection candidate is invalid")
        cases = tuple(_case_result_from_record(item) for item in cases_value)
        candidate = ReasoningCandidateResult(
            model,
            _decimal_value(candidate_value.get("configured_cost")),
            cases,
        )
        if candidate.passed != passed:
            raise ValueError("selection candidate result is inconsistent")
        candidates.append(candidate)
    return ReasoningEvaluationReport(tuple(candidates), selected_model)


def _case_result_from_record(value: object) -> ReasoningEvalCaseResult:
    if not isinstance(value, Mapping):
        raise ValueError("selection case is invalid")
    case_id = value.get("case_id")
    failure_code = value.get("failure_code")
    gates_value = value.get("gates")
    passed = value.get("passed")
    gate_names = ("source_anchors", "critical_tokens", "negation", "speaker_dialogue")
    if (
        not isinstance(case_id, str)
        or not case_id
        or (failure_code is not None and not isinstance(failure_code, str))
        or not isinstance(gates_value, Mapping)
        or set(gates_value) != set(gate_names)
        or type(passed) is not bool
    ):
        raise ValueError("selection case is invalid")
    gates = {name: gates_value[name] for name in gate_names}
    if any(type(gate) is not bool for gate in gates.values()):
        raise ValueError("selection case gates are invalid")
    return ReasoningEvalCaseResult(case_id, passed, failure_code, gates)


def _decimal_value(value: object) -> Decimal:
    if not isinstance(value, str) or not value:
        raise ValueError("selection cost is invalid")
    try:
        result = Decimal(value)
    except (ArithmeticError, ValueError) as error:
        raise ValueError("selection cost is invalid") from error
    if not result.is_finite() or result < 0:
        raise ValueError("selection cost is invalid")
    return result


def select_reasoning_model(
    cases: Sequence[ReasoningEvalCase], candidates: Sequence[ReasoningModelCandidate]
) -> ReasoningModelSelection:
    """Select the cheapest fully source-faithful configured model offline.

    This production-facing configuration boundary only evaluates deterministic
    evidence. Runtime composition can use the resulting model identifier to
    construct its injected transport; this function never imports or calls one.
    """
    try:
        report = evaluate_reasoning_candidates(cases, candidates)
        if report.selected_model is None:
            raise ValueError("no candidate passed the complete corpus")
        candidate = next(
            result
            for result in report.candidates
            if result.model == report.selected_model
        )
        return ReasoningModelSelection(
            report.selected_model, candidate.configured_cost, report
        )
    except (StopIteration, TypeError, ValueError):
        raise ReasoningSelectionError() from None
