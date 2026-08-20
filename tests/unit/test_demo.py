"""Focused safety and evidence tests for the reference demo."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_missing_fixture_files_fail_closed(tmp_path: Path) -> None:
    """Fixture changes that omit required evidence must stop before rendering."""
    from poddown.demo import _load_reference_fixture

    with pytest.raises(ValueError, match="missing fixture"):
        _load_reference_fixture(tmp_path)


def test_non_synthetic_voice_fails_before_rendering(tmp_path: Path) -> None:
    """A real voice must never cross the deterministic-local render boundary."""
    from poddown.demo import _load_reference_fixture

    fixture = Path(__file__).parents[2] / "integrations" / "reference-demo" / "v1"
    copied = tmp_path / "fixture"
    copied.mkdir()
    for source in fixture.iterdir():
        target = copied / source.name
        target.write_bytes(source.read_bytes())
    (copied / "voices.yaml").write_text(
        (copied / "voices.yaml")
        .read_text(encoding="utf-8")
        .replace("synthetic: true", "synthetic: false", 1),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="synthetic"):
        _load_reference_fixture(copied)


def test_unapproved_voice_fails_before_rendering(tmp_path: Path) -> None:
    """A missing local consent approval must stop before rendering."""
    from poddown.demo import _load_reference_fixture

    fixture = Path(__file__).parents[2] / "integrations" / "reference-demo" / "v1"
    copied = tmp_path / "fixture"
    copied.mkdir()
    for source in fixture.iterdir():
        target = copied / source.name
        target.write_bytes(source.read_bytes())
    (copied / "voices.yaml").write_text(
        (copied / "voices.yaml")
        .read_text(encoding="utf-8")
        .replace("consent_status: approved", "consent_status: pending", 1),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="approved"):
        _load_reference_fixture(copied)


def test_public_runner_has_no_live_mode_argument(tmp_path: Path) -> None:
    """Adding a live dispatch mode would violate the reference-demo boundary."""
    from poddown.demo import run_reference_demo

    with pytest.raises(TypeError):
        run_reference_demo(tmp_path, live=True)  # type: ignore[call-arg]


def test_result_serializes_to_json(tmp_path: Path) -> None:
    """Result evidence must remain portable and inspectable as JSON."""
    from poddown.demo import run_reference_demo

    result = run_reference_demo(tmp_path / "output")
    assert json.loads(json.dumps(result.to_dict(), sort_keys=True))["cost"] == "0"


def test_usage_counts_the_deliberate_failed_render_invocation(tmp_path: Path) -> None:
    """Removing the fail-once call from usage would understate demo provenance."""
    from poddown.demo import run_reference_demo

    output = tmp_path / "output"
    result = run_reference_demo(output)
    expected_requests = len(result.selected_candidate_ids) * 3 + 1

    assert result.usage == {"render_requests": expected_requests}
    assert json.loads((output / "usage.json").read_text(encoding="utf-8")) == {
        "cost": "0",
        "render_requests": expected_requests,
    }


def test_output_file_is_rejected(tmp_path: Path) -> None:
    """A file cannot safely contain the demo's staged evidence directories."""
    from poddown.demo import run_reference_demo

    output = tmp_path / "output"
    output.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ValueError, match="directory"):
        run_reference_demo(output)


def test_resume_rejects_missing_package_evidence(tmp_path: Path) -> None:
    """A replay cannot trust result.json when the immutable package is absent."""
    from poddown.demo import run_reference_demo

    output = tmp_path / "output"
    run_reference_demo(output)
    manifest = next((output / "packages").glob("*.json"))
    manifest.unlink()

    with pytest.raises(ValueError, match="package evidence"):
        run_reference_demo(output, resume=True)


def test_resume_rejects_corrupt_publication_evidence(tmp_path: Path) -> None:
    """A replay must authenticate the published bytes before reporting success."""
    from poddown.demo import run_reference_demo

    output = tmp_path / "output"
    run_reference_demo(output)
    published = next((output / "published").rglob("episode.wav"))
    published.unlink()

    with pytest.raises(ValueError, match="publication evidence"):
        run_reference_demo(output, resume=True)


def test_resume_rejects_tampered_result_evidence(tmp_path: Path) -> None:
    """A replay cannot authenticate mutable result fields from their shape alone."""
    from poddown.demo import run_reference_demo

    output = tmp_path / "output"
    run_reference_demo(output)
    result_path = output / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["critical_token_accuracy"] = 0.5
    result_path.write_text(json.dumps(result), encoding="utf-8")

    with pytest.raises(ValueError, match="result evidence"):
        run_reference_demo(output, resume=True)


def test_status_history_records_publication_before_completion(tmp_path: Path) -> None:
    """Resumable status evidence must expose the successful publication stage."""
    from poddown.demo import run_reference_demo

    output = tmp_path / "output"
    run_reference_demo(output)
    status = json.loads((output / "status.json").read_text(encoding="utf-8"))

    assert status["stage"] == "completed"
    assert "published" in status["history"]
    assert status["history"][-1] == "completed"


def test_demo_command_is_registered_and_documented() -> None:
    """Removing the local demo entrypoint would make the reference path unusable."""
    root = Path(__file__).parents[2]
    assert 'poddown-demo = "poddown.demo:main"' in (root / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert "demo:" in (root / "Makefile").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "--output" in readme
    assert "--resume" in readme
