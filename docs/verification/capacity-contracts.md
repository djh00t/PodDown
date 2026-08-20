# O10–O11 capacity and fault-boundary verification

## Scope

The local load contract admits or rejects work before dispatch using explicit
limits for in-flight work, queue age, and estimated cost. Rejections have stable,
redacted reasons and never include source, transcript, audio, or provider
payloads.

## Local evidence

```text
PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  pytest -p pytest_bdd.plugin -q \
  tests/bdd/test_capacity_contracts.py tests/unit/test_capacity.py
7 passed
```

Boundary tests cover exact limit admission, concurrency exhaustion, queue-age
overflow, cost-ceiling overflow, malformed limits, and invalid observations.

## Evidence boundary

This is a deterministic admission contract, not a production load test or a
measured capacity threshold. Provider outage drills, budget exhaustion against a
live provider, and deployment-scale load tests remain open.
