# C07 evidence-truth BDD coverage

## RED

`PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD .venv/bin/pytest -q -p pytest_bdd.plugin tests/bdd/test_production_closure_evidence_truth.py` failed as expected with 14 `StepDefinitionNotFoundError` failures before the C07 bindings existed.

## GREEN

The same focused BDD command passed 14 scenarios after adding test-only bindings. The scenarios establish that script-derived deterministic-local and host-local records are accepted only as non-live, reject false live eligibility, reject each required live-provider gate when invalid, and accept the complete live-shaped record only at numeric `1.0` token accuracy.

## Validation

- `uv run ruff format --check tests/bdd/test_production_closure_evidence_truth.py`
- `uv run ruff check tests/bdd/test_production_closure_evidence_truth.py`
- `uv run mypy --strict tests/bdd/test_production_closure_evidence_truth.py`
- `git diff --check`

All listed checks passed. Broad suites were intentionally not run.

## Commit SHA

The exact C07 commit SHA is recorded in the delivery handoff after this self-referential report is committed.

## Residual risk

This is validator-level BDD coverage only. It proves contract enforcement without performing a live-provider call or publication flow.
