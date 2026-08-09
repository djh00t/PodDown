# M1 Content Intelligence Verification

## Scope and provenance

- Verification baseline SHA: `83041c1d6758a4ccec0b45f5123bd1440e591f3a`.
- Task-owned additions: `tests/evals/test_content_intelligence.py` and
  `tests/fixtures/content/adversarial-adaptation.json`.
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

A disposable mutation copied `poddown` to a self-cleaning temporary directory,
disabled the adaptation and orchestration source-fidelity gates there, and
accepted the changed-number proposal. The repository source was never modified;
the real focused eval remains green.

## Commands and fresh evidence

The environment uses `PYDANTIC_DISABLE_PLUGINS=1` because automatic third-party
Pydantic plugin discovery is not required for this deterministic local suite.
Every command also sets `PYTHONPATH=src` so tests resolve the checked-out source.

| Command | Result |
| --- | --- |
| `python -m json.tool tests/fixtures/content/adversarial-adaptation.json` | Valid JSON. |
| `pytest tests/evals/test_content_intelligence.py -v` | 10 passed. |
| `make check` | 218 passed, 1 `live_provider` test deselected, 87.01% total coverage; Ruff and strict mypy passed. |
| `make build` | Blocked: `uv build` remained in Hatchling `build_sdist` without an artifact or completion status; stopped exact orphaned Task 7 processes. |
| `make docs` | Blocked: `uv run pdoc poddown --output-directory docs/api` remained running without output files or completion status; stopped exact Task 7 processes. |
| `git diff --check` | Pending final run after this evidence update. |

The final commit SHA is reported with the completion handoff. The verification
baseline above is the exact checked-out SHA before this Task 7 commit, avoiding
an impossible self-referential commit hash inside the committed evidence file.
Build and docs evidence remains explicitly blocked; no package or generated-doc
claim is made until the repository build tooling completes in a healthy runtime.
