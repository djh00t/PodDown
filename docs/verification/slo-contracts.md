# O03–O05 SLO contract verification

## Scope

The local readiness boundary now defines bounded service-level objectives and
measurements for availability, queue age, render completion, provider failure,
QA repair rate, and cost variance. Measurements contain only SLO identity,
comparison, target, observed value, unit, sample count, and pass/fail state.

## Local evidence

```text
PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  pytest -p pytest_bdd.plugin -q \
  tests/bdd/test_slo_contracts.py tests/unit/test_slo_contracts.py
9 passed
```

The contract rejects non-finite, negative, unbounded, or malformed objective
values and supports both `at_least` and `at_most` comparisons.

## Evidence boundary

This is a deterministic measurement contract, not evidence that a production
telemetry backend, dashboard, alert, or SLO history exists. Those remain
deployment and operations work.
