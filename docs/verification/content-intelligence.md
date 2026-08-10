# M1 Content Intelligence Verification

## Scope and provenance

- Verification baseline SHA: `83041c1d6758a4ccec0b45f5123bd1440e591f3a`.
- Task-owned additions: `tests/evals/test_content_intelligence.py`,
  `tests/fixtures/content/adversarial-adaptation.json`, plus the disposable
  `scripts/verify_content_mutation.py` verifier. The sdist configuration in
  `pyproject.toml` excludes local caches and delivery metadata from artifacts.
  The `Makefile` test target explicitly loads only pytest-bdd and coverage.
- Fixture SHA-256: `68fec3c23e2c7e647a11c8e69f9baecd999b7aaff287158733b3f48664fdf096`.
- Eval SHA-256: `c34840e44ee571d6a8232ebbb88fed7f5a12df77def6549759f2096b0df53976`.
- No live AI, voice, or renderer provider is invoked. The eval request uses the
  deterministic fixture reasoning port and an inert renderer sentinel.

## Adversarial corpus

The focused corpus asserts fail-closed source fidelity for an absent plausible
claim, changed number, unit, and date, inserted and removed negation, repeated
homographs and negations, code and table source blocks, and tampered source
anchors. Rejections assert the stable `unsupported_claim` or `missing_anchor`
code and ensure neither the source nor caller mutation appears in the error.

A reproducible disposable mutation is provided by
`PYDANTIC_DISABLE_PLUGINS=1 .venv/bin/python scripts/verify_content_mutation.py`.
It copies `src/` and `tests/` to a self-cleaning temporary directory, replaces
the exact `source_tokens[token] < count` adaptation guard and the exact
`character_index < 0` orchestration source-token guard with no-op branches, and
expects the changed-number and changed-date eval nodes to fail. The observed
result is `2 failed, 8 passed`; the real focused eval remains green and the
repository source is never modified.

## Commands and fresh evidence

The environment uses `PYDANTIC_DISABLE_PLUGINS=1` because automatic third-party
Pydantic plugin discovery is not required for this deterministic local suite.
The focused checked-out-source test commands set `PYTHONPATH=src` so tests
resolve the repository source. The mutation verifier replaces that with the
isolated copy's source path.

| Command | Result |
| --- | --- |
| `python -m json.tool tests/fixtures/content/adversarial-adaptation.json` | Valid JSON. |
| `pytest tests/evals/test_content_intelligence.py -v` | 10 passed. |
| `PYDANTIC_DISABLE_PLUGINS=1 .venv/bin/python scripts/verify_content_mutation.py` | Expected mutation kill: 2 named evals failed, 8 passed; verifier exited 0. |
| `make check` | 218 passed, 1 `live_provider` test deselected, 87.01% total coverage; Ruff and strict mypy passed. |
| `make check` after explicit plugin-loading hardening | Host-blocked while importing `pytest-bdd`/Pygments from the iCloud-backed virtualenv; the exact process was interrupted after no test collection. |
| `make build` | Passed: Hatchling built the source distribution and wheel. |
| `make docs` | Blocked: `uv run pdoc poddown --output-directory docs/api` remained running without output files or completion status; stopped exact Task 7 processes. |
| `git diff --check` | Passed after the Task 7 hardening changes. |

The final commit SHA is reported with the completion handoff. The verification
baseline above is the exact checked-out SHA before this Task 7 commit, avoiding
an impossible self-referential commit hash inside the committed evidence file.
Documentation evidence remains explicitly blocked by the local pdoc process; no
generated-doc claim is made until that tool completes in a healthy runtime.
