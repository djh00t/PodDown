from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "integrations" / "signal-supply" / "v1"


def test_signal_supply_fixture_is_complete_and_versioned():
    expected = {
        "show-profile.yaml",
        "article.md",
        "adaptation.json",
        "voices.yaml",
        "lexicons.yaml",
        "disclosure.yaml",
        "publishing-target.yaml",
        "github-workflow.yaml",
        "evals.json",
        "spoken-transcript.txt",
    }
    assert {path.name for path in FIXTURE.iterdir()} == expected
    assert (
        yaml.safe_load((FIXTURE / "show-profile.yaml").read_text())["version"]
        == "1.0.0"
    )
    assert (
        json.loads((FIXTURE / "evals.json").read_text())["fixture_version"] == "1.0.0"
    )


def test_fixture_contains_no_credentials_or_live_provider_identifiers():
    text = "\n".join(path.read_text(encoding="utf-8") for path in FIXTURE.iterdir())
    assert "sk-" not in text
    assert "secret_value" not in text
    assert "live" not in text.lower()
    assert "demo-only" in text
