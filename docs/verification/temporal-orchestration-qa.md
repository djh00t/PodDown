# Temporal orchestration and audio QA verification

## Scope

This bounded M2 slice adds a resumable Temporal workflow boundary over the
durable local audio contracts. It proves stable workflow and activity
identities, bounded transient retry, three-take fan-out, hard-gate candidate
selection, deterministic ranking, failed-segment repair, structured terminal
failure, and completed-workflow replay.

The implementation is deliberately offline. The integration test starts the
SDK's local Temporal development environment and registers deterministic local
activities. It does not contact a hosted Temporal service, a live provider, or
an external publication target. Local fixture mode is labelled
`deterministic-local-demo` and is not live-provider quality evidence.

## Acceptance evidence

BDD coverage is in
[`temporal_orchestration.feature`](../../tests/features/temporal_orchestration.feature)
with bindings in
[`test_temporal_orchestration.py`](../../tests/bdd/test_temporal_orchestration.py).
The scenarios cover:

- no more than three takes per segment attempt;
- hard-gate failure taking precedence over a higher soft score;
- transient activity retry without duplicate accepted cost evidence;
- repair of only failed segments; and
- replay of a completed workflow without new dispatches or cost events.

The local Temporal integration is in
[`test_temporal_orchestration.py`](../../tests/integration/test_temporal_orchestration.py).
It exercises a transient take failure, a failed segment repaired on attempt two,
stable accepted decisions, and a second service call against the same workflow
identity.

The deterministic BDD adapter records one accepted cost event per selected
segment and proves that replay adds no dispatches or cost events. The Temporal
integration proves stable activity keys, retry behavior, and no new activity
calls on completed-workflow replay. Provider-side exactly-once dispatch and
durable cost-ledger persistence are deliberately deferred to the next M2
provider-bound activity milestone; this evidence is not live-provider billing
evidence.

## Validation record

The focused BDD, unit, and local Temporal integration command passed `74` tests
in `1.61s`. The changed-scope gate then passed `make check`: `399 passed, 1
deselected`, with `87.97%` total branch coverage. Ruff format/check and strict
mypy passed across the repository (`32` source files for mypy). `make build`
produced the source distribution and wheel, and `make docs` generated the API
documentation. `uv lock --check` and `uv pip check` passed; `10` tracked
JSON/YAML files parsed successfully; `compileall`, `git diff --check`, and the
credential scan passed.

The lockfile resolves `temporalio` to `1.30.0`, selected through the repository
dependency-advisor policy and kept within the declared `<1.31` runtime range.

The focused branch-aware coverage gate over the new modules passed `74` tests:
diagnostics `100%`, selection `100%`, workflow `90%`, and orchestration `83%`.
This slice therefore has module-level evidence in addition to the repository
aggregate threshold.

The local Temporal test server is bounded by the test context manager and is
only an offline deterministic integration check. Hosted Temporal capacity,
worker deployment, provider dispatch, provider billing reconciliation, and
listening-quality calibration remain unproven here.

## Explicit deferrals

This slice does not claim final transcription, provider-backed pronunciation
verification, ffmpeg mastering, final episode packages, publication, database
persistence, object storage, API job endpoints, CLI/MCP execution, tenancy, or
live-provider quality evidence. Those capabilities remain owned by later M2,
M3, M4, and M5 milestones in the delivery plan.
