from __future__ import annotations

import json
from pathlib import Path

from poddown.qa.fidelity import evaluate_critical_tokens

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "integrations" / "signal-supply" / "v1"


def test_signal_supply_eval_fixture_requires_fidelity_and_source_bound_uncertainty():
    evals = json.loads(
        (ROOT / "integrations/signal-supply/v1/evals.json").read_text(encoding="utf-8")
    )
    assert evals["critical_token_fidelity"] == 1.0
    assert evals["preserve_counter_thesis"] is True
    assert evals["preserve_uncertainty"] is True
    assert evals["unsupported_promotional_claims"] == []
    assert evals["provider_calls"] == 0

    proposal = json.loads((FIXTURE / "adaptation.json").read_text(encoding="utf-8"))
    transcript = (FIXTURE / "spoken-transcript.txt").read_text(encoding="utf-8")
    fidelity = evaluate_critical_tokens(
        tuple(item["spoken_form"] for item in proposal["expected_critical_tokens"]),
        transcript,
    )
    assert fidelity.passed is True
    assert fidelity.accuracy == 1.0


def test_removing_fixture_does_not_add_signal_supply_core_logic():
    source_root = ROOT / "src" / "poddown"
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in source_root.rglob("*.py")
    )
    assert "signal-supply" not in source
    assert "Signal & Supply" not in source
    assert "portfolio" not in source.lower()
