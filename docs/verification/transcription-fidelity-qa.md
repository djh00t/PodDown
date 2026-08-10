# Transcription and fidelity QA verification

## Scope

This M2 slice reconciles the provider transcription contract to the verified
audio-byte activity boundary and feeds normalized transcript text into the
deterministic critical-token gate. It records transcript provider, mode,
model, request ID, the SHA-256 of the exact audio bytes, usage, confidence when
supplied, and decimal estimated cost. A filesystem transcription record stores
that response and an independent cost event atomically before quality is
evaluated; a filesystem quality record is then written atomically so replay
does not dispatch the renderer or transcriber again.

The default activity path remains an explicit `deterministic-local` fixture. It
uses the canonical expected-spoken text, records zero transcription cost, and
does not claim hosted-provider evidence. Provider-backed tests use sanitized
in-process fakes; no live provider credentials or network dispatch were used.

## Acceptance evidence

BDD scenarios in
[`provider_temporal_render.feature`](../../tests/features/provider_temporal_render.feature)
and bindings in
[`test_provider_temporal_render.py`](../../tests/bdd/test_provider_temporal_render.py)
prove:

- injected normalized transcript text drives critical-token fidelity;
- transcript checksums are bound to the verified audio bytes and empty text
  fails closed;
- missing critical tokens produce segment-only rerender evidence;
- malformed transcription fails closed without falling back to canonical text;
- bounded retry exhaustion reports the `transcription` failed gate;
- persisted quality replay keeps both renderer and transcription dispatch count
  at one, including after the render record is removed; and
- a simulated quality-write crash reuses the atomic transcription response and
  cost event without a second transcription dispatch; and
- deterministic local quality is explicitly labeled and zero-cost.

The normalized provider contract and OpenAI adapter are covered by
[`test_openai_transcription.py`](../../tests/contract/providers/test_openai_transcription.py).
Candidate-quality serialization and immutable filesystem quality records are
covered by
[`test_selection.py`](../../tests/unit/audio/test_selection.py) and
[`test_storage.py`](../../tests/unit/audio/test_storage.py).

## Verification record

Changed-scope quality gate:

```text
PYDANTIC_DISABLE_PLUGINS=1 make check
454 passed, 1 live-provider test deselected; branch coverage 87.79%; Ruff and strict mypy passed
```

Branch-aware audio/provider gate:

```text
PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
.venv/bin/pytest -p pytest_bdd.plugin -p pytest_cov.plugin \
tests/unit/audio tests/contract/providers/test_openai_transcription.py \
tests/bdd/test_provider_temporal_render.py \
tests/integration/test_temporal_durable_render_activity.py \
tests/integration/test_temporal_failure_semantics.py \
tests/integration/test_durable_render.py \
tests/integration/test_durable_render_concurrency.py \
tests/integration/test_durable_render_multiprocess.py \
tests/integration/test_temporal_orchestration.py \
--cov=poddown.audio.activities --cov=poddown.audio.contracts \
--cov=poddown.audio.render --cov=poddown.audio.selection \
--cov=poddown.audio.storage --cov=poddown.audio.workflow \
--cov=poddown.providers.contracts --cov=poddown.providers.openai_transcription \
--cov-branch --cov-fail-under=80 -q
231 passed in 12.58s; total branch coverage 88.95%
activities 89%, contracts 93%, render 82%, selection 90%, storage 85%, workflow 92%, provider contracts 91%, OpenAI adapter 95%
```

Additional checks passed:

- `make build` produced `dist/poddown_studio-0.0.0.tar.gz` and
  `dist/poddown_studio-0.0.0-py3-none-any.whl`.
- `make docs` generated API documentation.
- `uv lock --check` resolved 34 packages without lock drift.
- `uv pip check` reported all 33 installed packages compatible.
- `10` tracked JSON/YAML files parsed successfully.
- `compileall -q src tests`, `git diff --check`, and the credential-pattern
  audit passed with no findings.

## Boundaries and deferrals

This evidence is deterministic local/provider-contract evidence, not a live
OpenAI transcription demonstration. Pricing remains an injected estimator;
the adapter's default fixture estimator is zero. The persisted event is
estimated provider cost keyed by the provider request ID; external invoice
reconciliation belongs to the M3 metering milestone. Final-master transcription,
mastering, pronunciation-provider verification, package commit, publication,
API/CLI/MCP execution, tenancy, and production readiness remain later
milestones. A worker crash between an external provider response and the local
atomic transcription-record commit remains an external billing-reconciliation
boundary; once the record is committed, quality replay is locally resumable and
does not redispatch transcription.
