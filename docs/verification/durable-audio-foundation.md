# Durable audio foundation verification

## Scope

This bounded M2 slice proves rights and renderer-capability preflight,
deterministic local multi-take rendering, content-addressed immutable artifacts,
provider usage/cost recording, and replay idempotency. It runs entirely in
deterministic local-fixture mode; it makes no live-provider or production claim.

The primary acceptance coverage is
[`durable_audio.feature`](../../tests/features/durable_audio.feature), with
audio unit coverage in [`tests/unit/audio`](../../tests/unit/audio) and a
restart-boundary integration regression in
[`test_durable_render.py`](../../tests/integration/test_durable_render.py).

## Persistence evidence

`test_filesystem_records_replay_across_fresh_service_instances_and_attempts`
renders three rights-cleared local takes, captures immutable artifact bytes and
durable cost events, then recreates the artifact store, record store, service,
and renderer against the same filesystem roots. The fresh renderer receives zero
calls during replay; persisted candidates, SHA-256 values, cost events, and
artifact bytes remain identical. An `attempt=2` render produces a distinct
candidate identity and artifact digest, with one new renderer call.

## Validation record

The test was written first. Its initial run was red because the test passed the
public keyword-only `take_count` argument positionally; after correcting that
test invocation, the isolated integration test passed: `1 passed in 0.04s`.

The literal focused command from the task brief was also run:

```sh
PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src \
uv run pytest tests/integration/test_durable_render.py \
tests/bdd/test_durable_audio.py tests/unit/audio tests/unit/test_rendering.py -q \
--cov=poddown.audio --cov-branch --cov-report=term-missing
```

It correctly demonstrated the known autoload distinction: with
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `pytest-cov` is not loaded, so pytest exits
with `unrecognized arguments: --cov=poddown.audio --cov-branch
--cov-report=term-missing`. The passing equivalent explicitly loads both
repository-required plugins:

```sh
PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src \
uv run pytest -p pytest_bdd.plugin -p pytest_cov.plugin \
tests/integration/test_durable_render.py tests/bdd/test_durable_audio.py \
tests/unit/audio tests/unit/test_rendering.py -q --cov=poddown.audio \
--cov-branch --cov-report=term-missing
```

Result: `117 passed in 0.33s`; `poddown.audio` branch coverage was `88.68%`
(the repository threshold is 80%). The selected suite exercises preflight
rejection, new rendering, replay, and persisted-artifact integrity failures.

`PYDANTIC_DISABLE_PLUGINS=1 PYTHONPATH=src make check` ran its test target
successfully: `325 passed, 1 deselected in 2.82s`, with `87.32%` total branch
coverage. Its subsequent format check failed only because the unowned
`tests/bdd/test_durable_audio.py` would be reformatted; this evidence slice did
not modify that file. `ruff check src tests` passed, strict mypy passed for 28
source files, and `make build` produced the source distribution and wheel.
`git diff --check` produced no errors.

## Deferred work

Temporal orchestration, live providers, transcription, diagnostics, mastering,
packaging, CLI/API/MCP, and publishing are explicitly deferred to later plans.
