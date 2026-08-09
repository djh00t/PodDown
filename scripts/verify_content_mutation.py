"""Verify that weakening source-fidelity gates breaks the M1 adversarial evals."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_ADAPTATION_TARGET = """    for token, count in script_tokens.items():
        if source_tokens[token] < count:
"""
_ADAPTATION_MUTATION = """    for token, count in script_tokens.items():
        if False:  # disposable mutation: bypass adaptation token-count validation
"""
_SERVICE_TARGET = """        if character_index < 0:
            raise AdaptationError("unsupported_claim")
"""
_SERVICE_MUTATION = """        if False:  # disposable source-token mutation
            raise AdaptationError("unsupported_claim")
"""
_EXPECTED_FAILURES = (
    "test_adversarial_literal_mutations_fail_closed_before_renderer[changed-number]",
    "test_adversarial_literal_mutations_fail_closed_before_renderer[changed-date]",
    "2 failed, 8 passed",
)


def _apply_mutation(path: Path, target: str, mutation: str) -> None:
    source = path.read_text(encoding="utf-8")
    if source.count(target) != 1:
        raise RuntimeError(f"mutation target is not unique: {path}")
    path.write_text(source.replace(target, mutation), encoding="utf-8")


def main() -> int:
    """Run the eval suite against an isolated, deliberately weakened copy."""
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="poddown-mutation-") as temp:
        work = Path(temp)
        shutil.copytree(root / "src", work / "src")
        shutil.copytree(root / "tests", work / "tests")
        _apply_mutation(
            work / "src/poddown/content/adaptation.py",
            _ADAPTATION_TARGET,
            _ADAPTATION_MUTATION,
        )
        _apply_mutation(
            work / "src/poddown/content/service.py",
            _SERVICE_TARGET,
            _SERVICE_MUTATION,
        )

        environment = os.environ.copy()
        environment.update(
            {
                "PYDANTIC_DISABLE_PLUGINS": "1",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "PYTHONPATH": str(work / "src"),
            }
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/evals/test_content_intelligence.py",
                "-q",
                "-o",
                "addopts=",
            ],
            cwd=work,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        output = f"{result.stdout}{result.stderr}"
        print(output, end="")
        if result.returncode == 0 or any(
            expected not in output for expected in _EXPECTED_FAILURES
        ):
            print("mutation check failed: expected source-fidelity eval failures")
            return 1
        print("mutation check passed: weakened gates caused the expected eval failures")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
