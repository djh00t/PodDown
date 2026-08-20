"""BDD bindings for the fail-closed release evidence gate."""

from __future__ import annotations

from typing import Any

from pytest_bdd import given, scenarios, then, when

from poddown.release import ReleaseCheck, ReleaseEvidence

scenarios("../features/release_evidence.feature")

CHECKS = {check: True for check in ReleaseCheck}


@given("a complete release evidence bundle")
def complete_release(context: Any) -> None:
    context.values["evidence"] = ReleaseEvidence(
        version="1.2.3",
        source_commit="a" * 40,
        lock_sha256="b" * 64,
        artifact_sha256={"poddown.whl": "c" * 64},
        checks=CHECKS,
        skipped_external=(),
    )


@given("a release evidence bundle with a skipped security check")
def skipped_security_release(context: Any) -> None:
    checks = dict(CHECKS)
    checks[ReleaseCheck.SBOM] = False
    context.values["evidence"] = ReleaseEvidence(
        version="1.2.3",
        source_commit="a" * 40,
        lock_sha256="b" * 64,
        artifact_sha256={"poddown.whl": "c" * 64},
        checks=checks,
        skipped_external=("SBOM generation was not available",),
    )


@when("the release gate is evaluated")
def evaluate_release(context: Any) -> None:
    context.values["result"] = context.values["evidence"].to_dict()


@then("the release evidence is ready")
def release_is_ready(context: Any) -> None:
    result = context.values["result"]
    assert result["ready"] is True
    assert result["blocking_checks"] == []


@then("the release evidence is blocked with the missing check")
def release_is_blocked(context: Any) -> None:
    result = context.values["result"]
    assert result["ready"] is False
    assert result["blocking_checks"] == ["sbom"]
    assert result["skipped_external"] == ["SBOM generation was not available"]
