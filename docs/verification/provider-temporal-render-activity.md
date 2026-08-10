# Provider-bound Temporal render activity verification

## Scope

This M2 slice binds the existing `DurableRenderService` to the Temporal render
activity name `poddown.audio.render_segment`. The handler authenticates the
episode snapshot and stable activity key, checks voice consent through the
durable service, persists immutable filesystem artifacts and cost events, reads
the verified artifact, and returns hard-gated `CandidateQuality` evidence.

The default evaluator is explicitly `deterministic-local-demo` behavior. It
uses the request's expected spoken text as a deterministic transcript and does
not claim a transcription-provider call. The local renderer is deterministic,
offline, zero-cost, and has no hosted-provider credentials.

## Acceptance evidence

BDD coverage is in
[`provider_temporal_render.feature`](../../tests/features/provider_temporal_render.feature)
with bindings in
[`test_provider_temporal_render.py`](../../tests/bdd/test_provider_temporal_render.py).
It proves:

- a consented activity persists one candidate artifact and one cost event;
- a post-persistence retry replays the same durable outcome without a second
  local renderer dispatch; and
- a provider consent mismatch returns a non-retryable rights error before any
  renderer call; and
- the workflow terminates that rights failure on attempt one with only the
  `rights` failed gate, without entering segment repair; and
- successful takes remain eligible when a sibling take exhausts transient
  retries, while the failed segment is not repaired.

The real Temporal worker integration is in
[`test_temporal_durable_render_activity.py`](../../tests/integration/test_temporal_durable_render_activity.py).
It registers the provider-bound activity, fans out the workflow's three takes,
fails once after take zero has been durably saved, and verifies three unique
renderer keys, one record and one cost event per take, no fourth renderer
dispatch, and identical completed-workflow replay.

The activity's stable payload boundary is covered by
[`test_activities.py`](../../tests/unit/audio/test_activities.py), the decoded
episode mapping contract by
[`test_workflow_contracts.py`](../../tests/unit/audio/test_workflow_contracts.py),
the concurrent local idempotency claim by
[`test_durable_render_concurrency.py`](../../tests/integration/test_durable_render_concurrency.py),
the terminal workflow failure boundary by
[`test_temporal_failure_semantics.py`](../../tests/integration/test_temporal_failure_semantics.py),
the separate-process claim boundary by
[`test_durable_render_multiprocess.py`](../../tests/integration/test_durable_render_multiprocess.py),
and the local PCM clipping regression by
[`test_local.py`](../../tests/unit/audio/test_local.py).

## Focused verification record

The following focused commands passed during implementation:

```text
PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytest_bdd.plugin -p pytest_cov.plugin tests/unit/audio/test_activities.py tests/unit/audio/test_local.py tests/bdd/test_provider_temporal_render.py tests/integration/test_temporal_durable_render_activity.py --cov=poddown.audio.activities --cov-branch --cov-fail-under=80 -q
20 passed; poddown.audio.activities 100% branch coverage

PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytest_bdd.plugin tests/unit/audio/test_workflow_contracts.py -q
17 passed

uv run ruff check src/poddown/audio/activities.py src/poddown/audio/workflow.py src/poddown/audio/local.py src/poddown/audio/__init__.py tests/unit/audio/test_activities.py tests/unit/audio/test_local.py tests/bdd/test_provider_temporal_render.py tests/integration/test_temporal_durable_render_activity.py
All checks passed

uv run mypy src/poddown/audio
Success: no issues found
```

The final changed-scope gate passed `PYDANTIC_DISABLE_PLUGINS=1 make check`:
`429` selected tests, one live-provider test deselected, aggregate branch
coverage reported at `88%`, Ruff format/check passed, and strict mypy passed.
`make build` produced the source distribution and wheel; `make docs` generated
API documentation; `uv lock --check` and `uv pip check` passed; `10` tracked
JSON/YAML files validated; `compileall`, `git diff --check`, and the
credential-pattern scan passed.

The review-hardening focused suite passed after adding terminal failure and
concurrency claims:

```text
PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytest_bdd.plugin tests/bdd/test_provider_temporal_render.py tests/integration/test_temporal_failure_semantics.py tests/integration/test_temporal_durable_render_activity.py tests/integration/test_durable_render_concurrency.py tests/integration/test_temporal_orchestration.py tests/integration/test_durable_render.py tests/unit/audio/test_workflow_contracts.py tests/unit/audio/test_orchestration.py tests/unit/audio/test_render.py tests/unit/audio/test_artifacts.py -q
101 passed in 7.73s
```

The workflow-failure and cross-process claim checks passed alongside the BDD
suite:

```text
PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytest_bdd.plugin tests/bdd/test_provider_temporal_render.py tests/integration/test_temporal_failure_semantics.py tests/integration/test_durable_render_multiprocess.py -q
9 passed in 6.68s
```

The branch-aware focused coverage gate for the four changed audio modules also
passed:

```text
PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytest_bdd.plugin -p pytest_cov.plugin tests/unit/audio tests/integration/test_durable_render.py tests/integration/test_durable_render_concurrency.py tests/integration/test_durable_render_multiprocess.py tests/integration/test_temporal_durable_render_activity.py tests/integration/test_temporal_failure_semantics.py tests/integration/test_temporal_orchestration.py tests/bdd/test_durable_audio.py tests/bdd/test_temporal_orchestration.py tests/bdd/test_provider_temporal_render.py --cov=poddown.audio.activities --cov=poddown.audio.render --cov=poddown.audio.storage --cov=poddown.audio.workflow --cov-branch --cov-report=term-missing --cov-fail-under=80 -q
209 passed in 8.41s; total branch coverage 89.12%
activities 100%, render 82%, storage 88%, workflow 91%
```

## Explicit boundaries

This evidence is not hosted Temporal capacity, live-provider exactly-once
proof, provider billing reconciliation, transcription-provider fidelity,
pronunciation-provider verification, mastering, package commit, publication,
API job durability, CLI/MCP execution, tenancy, or production readiness.
The POSIX filesystem claim prevents concurrent local worker processes from
dispatching the same key twice, but a provider or process crash after dispatch
and before the durable record save still requires provider-side idempotency and
reconciliation. Those later provider/platform responsibilities remain open;
this slice proves the local durable recording, replay, and concurrency boundary
only.
