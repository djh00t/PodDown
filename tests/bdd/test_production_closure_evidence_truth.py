"""BDD coverage for production-closure evidence truth."""

from typing import Any

from pytest_bdd import given, parsers, scenarios, then, when

from poddown.evidence import validate_execution_evidence

scenarios("../features/production_closure_evidence_truth.feature")


@given(parsers.parse('a C07 "{mode}" record with "{render_evidence}" render evidence'))
def local_record(context: Any, mode: str, render_evidence: str) -> None:
    context.values["record"] = {
        "schema_version": "1.0",
        "mode": mode,
        "render_evidence": render_evidence,
        "transcript_evidence": "script-derived",
        "publication_scope": "filesystem",
        "live_eligible": False,
    }


@given("a complete C07 live-shaped evidence record")
def live_shaped_record(context: Any) -> None:
    context.values["record"] = {
        "schema_version": "1.0",
        "mode": "live-provider",
        "render_evidence": "provider-response",
        "transcript_evidence": "provider-asr",
        "publication_scope": "object-storage",
        "live_eligible": True,
        "renderer": {
            "provider": "elevenlabs",
            "model": "eleven_multilingual_v2",
            "request_ids": ["render-1"],
        },
        "transcriber": {
            "provider": "openai",
            "model": "gpt-4o-transcribe",
            "request_ids": ["asr-1"],
        },
        "consent_valid": True,
        "critical_token_accuracy": 1.0,
        "cost_evidence": {"currency": "USD", "estimated": 1.5, "reconciled": 1.5},
    }


@given("the C07 record is marked live eligible")
def mark_live_eligible(context: Any) -> None:
    context.values["record"]["live_eligible"] = True


@given(parsers.parse('the C07 "{gate}" live gate is invalid'))
def invalidate_live_gate(context: Any, gate: str) -> None:
    record = context.values["record"]
    invalidators = {
        "live-provider mode": lambda: record.__setitem__("mode", "host-local"),
        "provider render evidence": lambda: record.__setitem__(
            "render_evidence", "host-tts"
        ),
        "provider ASR evidence": lambda: record.__setitem__(
            "transcript_evidence", "script-derived"
        ),
        "valid consent": lambda: record.__setitem__("consent_valid", False),
        "renderer metadata": lambda: record.pop("renderer"),
        "transcriber metadata": lambda: record.pop("transcriber"),
        "cost evidence": lambda: record.pop("cost_evidence"),
    }
    invalidators[gate]()


@given(parsers.parse('C07 critical-token accuracy is "{accuracy}"'))
def set_critical_token_accuracy(context: Any, accuracy: str) -> None:
    context.values["record"]["critical_token_accuracy"] = (
        True if accuracy == "true" else float(accuracy)
    )


@given(parsers.parse('C07 transcript evidence is "{transcript_evidence}"'))
def set_transcript_evidence(context: Any, transcript_evidence: str) -> None:
    context.values["record"]["transcript_evidence"] = transcript_evidence


@given(parsers.parse('C07 publication scope is "{publication_scope}"'))
def set_publication_scope(context: Any, publication_scope: str) -> None:
    context.values["record"]["publication_scope"] = publication_scope


@given(parsers.parse('the C07 "{role}" provider is "{provider}"'))
def set_provider_identity(context: Any, role: str, provider: str) -> None:
    context.values["record"][role]["provider"] = provider


@when("C07 execution evidence is validated")
def validate_record(context: Any) -> None:
    try:
        context.values["validated"] = validate_execution_evidence(
            context.values["record"]
        )
    except ValueError as error:
        context.values["error"] = error


@then("the C07 record is accepted as non-live evidence")
def accepted_as_non_live(context: Any) -> None:
    assert context.values["validated"]["live_eligible"] is False


@then("the C07 record is accepted as live evidence")
def accepted_as_live(context: Any) -> None:
    assert context.values["validated"]["live_eligible"] is True


@then("the C07 record is rejected as false live evidence")
def rejected_as_false_live(context: Any) -> None:
    assert "error" in context.values
