"""Unit tests for release evidence validation and gating."""

import pytest

from poddown.release import ReleaseCheck, ReleaseEvidence


def _evidence(**overrides: object) -> ReleaseEvidence:
    values: dict[str, object] = {
        "version": "1.2.3",
        "source_commit": "a" * 40,
        "lock_sha256": "b" * 64,
        "artifact_sha256": {"poddown.whl": "c" * 64},
        "checks": {check: True for check in ReleaseCheck},
        "skipped_external": (),
    }
    values.update(overrides)
    return ReleaseEvidence(**values)  # type: ignore[arg-type]


def test_complete_release_evidence_is_ready_and_immutable() -> None:
    evidence = _evidence()
    assert evidence.to_dict() == {
        "artifact_sha256": {"poddown.whl": "c" * 64},
        "blocking_checks": [],
        "checks": {check.value: True for check in ReleaseCheck},
        "lock_sha256": "b" * 64,
        "ready": True,
        "skipped_external": [],
        "source_commit": "a" * 40,
        "version": "1.2.3",
    }


def test_failed_check_and_skipped_external_evidence_block_release() -> None:
    checks = {check: True for check in ReleaseCheck}
    checks[ReleaseCheck.SIGNATURE] = False
    evidence = _evidence(
        checks=checks,
        skipped_external=("signing key is deployment-owned",),
    )
    result = evidence.to_dict()
    assert result["ready"] is False
    assert result["blocking_checks"] == ["signature"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"version": "latest"},
        {"source_commit": "not-a-commit"},
        {"lock_sha256": "x"},
        {"artifact_sha256": {}},
        {"artifact_sha256": {"poddown.whl": "not-a-digest"}},
    ],
)
def test_release_identity_and_digests_are_strict(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _evidence(**overrides)


def test_checks_must_cover_every_release_gate() -> None:
    checks = {ReleaseCheck.TESTS: True}
    with pytest.raises(ValueError, match="release checks"):
        _evidence(checks=checks)
