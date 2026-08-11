"""BDD coverage for honest local execution evidence labels."""

from pytest_bdd import given, parsers, scenarios, then, when

from poddown.evidence import validate_execution_evidence

scenarios("../features/execution_evidence.feature")


@given(
    parsers.parse(
        'a "{mode}" execution record with "{render_evidence}" render evidence'
    )
)
def execution_record(context, mode: str, render_evidence: str) -> None:
    context.values["record"] = {
        "schema_version": "1.0",
        "execution_mode": mode,
        "render_evidence": render_evidence,
        "transcript_evidence": "script-derived",
        "publication_scope": "filesystem",
        "live_eligible": False,
    }


@when("execution evidence is validated")
def validate_record(context) -> None:
    context.values["validated"] = validate_execution_evidence(context.values["record"])


@then("the record is accepted as non-live evidence")
def accepted_as_non_live(context) -> None:
    assert context.values["validated"]["live_eligible"] is False
