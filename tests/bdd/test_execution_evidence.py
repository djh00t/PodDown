"""BDD coverage for honest local execution evidence labels."""

from typing import Any

from pytest_bdd import given, parsers, scenarios, then, when

from poddown.evidence import validate_execution_evidence

scenarios("../features/execution_evidence.feature")


@given(
    parsers.parse(
        'a "{mode}" execution record with "{render_evidence}" render evidence'
    )
)
def execution_record(context: Any, mode: str, render_evidence: str) -> None:
    context.values["record"] = {
        "schema_version": "1.0",
        "mode": mode,
        "render_evidence": render_evidence,
        "transcript_evidence": "script-derived",
        "publication_scope": "filesystem",
        "live_eligible": False,
    }


@given("a live-provider execution record with provider evidence and complete metadata")
def live_provider_execution_record(context: Any) -> None:
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


@when("execution evidence is validated")
def validate_record(context: Any) -> None:
    try:
        context.values["validated"] = validate_execution_evidence(
            context.values["record"]
        )
    except ValueError as error:
        context.values["error"] = error


@then("the record is accepted as non-live evidence")
def accepted_as_non_live(context: Any) -> None:
    assert context.values["validated"]["live_eligible"] is False


@then("the record is accepted as live evidence")
def accepted_as_live(context: Any) -> None:
    assert context.values["validated"]["live_eligible"] is True


@given("the execution record is marked live eligible")
def mark_live_eligible(context: Any) -> None:
    context.values["record"]["live_eligible"] = True


@given("an execution record with legacy execution_mode key")
def execution_record_with_legacy_execution_mode(context: Any) -> None:
    context.values["record"] = {
        "schema_version": "1.0",
        "execution_mode": "deterministic-local",
        "render_evidence": "synthetic-bytes",
        "transcript_evidence": "script-derived",
        "publication_scope": "filesystem",
        "live_eligible": False,
    }


@given("provider evidence uses singular request_id fields")
def legacy_request_id_fields(context: Any) -> None:
    record = context.values["record"]
    record["renderer"]["request_id"] = record["renderer"].pop("request_ids")[0]
    record["transcriber"]["request_id"] = record["transcriber"].pop("request_ids")[0]


@then("the record is rejected as false live evidence")
def rejected_as_false_live(context: Any) -> None:
    assert "error" in context.values
    assert "mode" in str(context.values["error"])


@then("the record is rejected as malformed execution evidence")
def rejected_as_malformed(context: Any) -> None:
    assert "error" in context.values
