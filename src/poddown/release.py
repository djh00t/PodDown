"""Fail-closed release provenance and security-evidence contracts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_SEMVER = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


class ReleaseCheck(StrEnum):
    """Evidence categories required before a release can be staged."""

    TESTS = "tests"
    LOCK = "lock"
    SBOM = "sbom"
    VULNERABILITY_SCAN = "vulnerability_scan"
    SIGNATURE = "signature"
    STAGED_DEPLOYMENT = "staged_deployment"


def _require_bounded_text(value: str, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 512
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(f"{field} must be bounded text")
    return value


def _require_digest(value: str, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True, slots=True)
class ReleaseEvidence:
    """Immutable evidence bundle for one reproducible release candidate."""

    version: str
    source_commit: str
    lock_sha256: str
    artifact_sha256: Mapping[str, str]
    checks: Mapping[ReleaseCheck | str, bool]
    skipped_external: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or _SEMVER.fullmatch(self.version) is None:
            raise ValueError("version must be a semantic version")
        if (
            not isinstance(self.source_commit, str)
            or _COMMIT.fullmatch(self.source_commit) is None
        ):
            raise ValueError("source_commit must be a hexadecimal commit")
        _require_digest(self.lock_sha256, field="lock_sha256")
        if not isinstance(self.artifact_sha256, Mapping) or not self.artifact_sha256:
            raise ValueError("artifact_sha256 must contain release artifacts")
        artifact_digests: dict[str, str] = {}
        for name, digest in self.artifact_sha256.items():
            _require_bounded_text(name, field="artifact name")
            if any(part in {"", ".", ".."} for part in name.split("/")):
                raise ValueError("artifact name contains path syntax")
            artifact_digests[name] = _require_digest(
                digest,
                field=f"artifact checksum for {name}",
            )

        normalized_checks: dict[ReleaseCheck, bool] = {}
        for raw_check, passed in self.checks.items():
            try:
                check = (
                    raw_check
                    if isinstance(raw_check, ReleaseCheck)
                    else ReleaseCheck(raw_check)
                )
            except (TypeError, ValueError) as error:
                raise ValueError("release check is unsupported") from error
            if type(passed) is not bool:
                raise ValueError("release check values must be boolean")
            normalized_checks[check] = passed
        if set(normalized_checks) != set(ReleaseCheck):
            raise ValueError("release checks must cover every release gate")
        skipped: list[str] = []
        for value in self.skipped_external:
            skipped.append(_require_bounded_text(value, field="skipped evidence"))
        object.__setattr__(self, "artifact_sha256", MappingProxyType(artifact_digests))
        object.__setattr__(self, "checks", MappingProxyType(normalized_checks))
        object.__setattr__(self, "skipped_external", tuple(skipped))

    def to_dict(self) -> dict[str, object]:
        """Return JSON-safe release evidence and its fail-closed gate result."""
        blocking = [check.value for check in ReleaseCheck if not self.checks[check]]
        return {
            "artifact_sha256": dict(sorted(self.artifact_sha256.items())),
            "blocking_checks": blocking,
            "checks": {check.value: self.checks[check] for check in ReleaseCheck},
            "lock_sha256": self.lock_sha256,
            "ready": not blocking and not self.skipped_external,
            "skipped_external": list(self.skipped_external),
            "source_commit": self.source_commit,
            "version": self.version,
        }


__all__ = ["ReleaseCheck", "ReleaseEvidence"]
