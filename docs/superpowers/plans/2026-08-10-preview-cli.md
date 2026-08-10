# Offline Preview CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a zero-cost `poddown preview <markdown>` command that validates Markdown/frontmatter, resolves a local profile name using the approved precedence rules, reports the exact source digest, and never dispatches a provider.

**Architecture:** Keep CLI parsing and exit-code mapping in `poddown.cli`, deterministic source/profile resolution and summary construction in `poddown.preview`, and module execution in `poddown.__main__`. Reuse `poddown.intake.validate_markdown` and `poddown.content.source.snapshot_source`; do not duplicate source validation or introduce rendering, storage, API, authentication, or provider calls.

**Tech Stack:** Python 3.12+, `argparse`, `tomllib`, PyYAML already used by intake, pytest-bdd, pytest, and the existing editable package entry point.

## Global Constraints

- Implement only the offline preview slice of `specs/005-cli-and-automation/spec.md`; `render`, `publish`, and `status` remain deferred.
- Configuration precedence is flags, document frontmatter, project config, user config, then the `default` profile name.
- Profile configuration is local metadata only; no voice dispatch, network call, credential read, package write, or provider import is permitted.
- Preserve source bytes exactly and compute SHA-256 from the original UTF-8 bytes.
- JSON output is sorted, compact, UTF-8, and stable across equivalent inputs.
- Validation errors exit with code `2`; successful preview exits with code `0`.
- Every behavior change has a pytest-bdd scenario before production implementation.
- Do not add dependencies or modify unrelated intake semantics.

---

### Task 1: Define the preview contract with failing BDD and unit tests

**Files:**
- Create: `tests/features/preview.feature`
- Create: `tests/bdd/test_preview.py`
- Create: `tests/unit/test_preview.py`
- Create: `tests/integration/test_preview_cli.py`

**Interfaces:**
- The tests will import `PreviewResult`, `PreviewValidationError`, `preview_markdown`, and `render_preview_json` from `poddown.preview`.
- The executable command will be `python -m poddown preview SOURCE [--profile PROFILE] [--config PATH] [--json]`.

- [x] **Step 1: Write BDD scenarios before production code.** Cover valid human output, deterministic JSON output, invalid frontmatter/profile with exit code 2, configuration precedence (flag over document over project over user over default), exact source hash preservation, and zero provider calls.

```gherkin
Scenario: Preview a valid local source without provider work
  Given a valid Markdown source and local profile configuration
  When the preview command runs
  Then it exits successfully with the source SHA-256 and resolved profile
  And it reports zero provider calls

Scenario: Emit stable JSON preview output
  Given a valid Markdown source and local profile configuration
  When the preview command runs with JSON output twice
  Then both outputs are byte-identical compact JSON

Scenario: Reject invalid Markdown metadata
  Given a Markdown source with invalid PodDown frontmatter
  When the preview command runs
  Then it exits with validation code 2 and a stable error

Scenario: Apply profile configuration precedence
  Given user, project, document, and flag profile values
  When the preview command runs with a profile flag
  Then the flag profile is selected
  When the preview command runs without a profile flag
  Then the document profile is selected
```

- [x] **Step 2: Add executable BDD bindings and deterministic fixtures.** Use temporary Markdown/config files, invoke the public preview function for direct assertions, and invoke `python -m poddown` only in the integration test. Assert no provider module or call is touched by checking the returned `provider_calls == 0` and preserving the source bytes after execution.

- [x] **Step 3: Add focused unit assertions.** Assert sorted compact JSON keys, exact UTF-8 SHA-256, stable human output, validation error mapping, TOML config parsing, frontmatter precedence, missing config fallback to `default`, and malformed config rejection.

- [x] **Step 4: Observe the expected red state.** Run:

```bash
uv run pytest -q tests/bdd/test_preview.py tests/unit/test_preview.py tests/integration/test_preview_cli.py
```

Expected result: collection fails because `poddown.preview`, `poddown.cli`, and the module entry point do not exist yet.

### Task 2: Implement deterministic preview resolution and output

**Files:**
- Create: `src/poddown/preview.py`
- Create: `src/poddown/cli.py`
- Create: `src/poddown/__main__.py`
- Modify: `src/poddown/intake.py` only if the tests demonstrate that an explicit flag cannot obey precedence through the existing public validator; preserve all existing call behavior.

**Interfaces:**
- `PreviewResult`, frozen dataclass with `source_sha256: str`, `profile_id: str`, `source_bytes: int`, `block_count: int`, and `provider_calls: int`.
- `PreviewValidationError(ValueError)` carries stable user-facing validation text and an `errors: tuple[str, ...]` field.
- `preview_markdown(source: bytes, *, source_name: str, flag_profile: str | None, project_config: Mapping[str, object] | None, user_config: Mapping[str, object] | None) -> PreviewResult`.
- `render_preview_json(result: PreviewResult) -> bytes` uses `json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.
- `main(argv: Sequence[str] | None = None) -> int` returns `0` for success and `2` for validation failures.

- [x] **Step 1: Implement immutable config and source resolution.** Read source bytes once, decode UTF-8 strictly, extract only the profile hint needed for precedence, call `validate_markdown` with the merged available profile names/default, and call `snapshot_source` only after validation succeeds. The returned digest must equal `sha256(source_bytes).hexdigest()` and block count must come from the immutable snapshot.

- [x] **Step 2: Implement human and JSON output.** Human output must contain the resolved profile, source digest, byte count, block count, and `Provider calls: 0`; JSON must contain the same stable fields and no filesystem path or transient timestamp.

- [x] **Step 3: Implement the CLI boundary.** Add the `preview` subcommand, `--profile`, `--config`, and `--json`; load project TOML beside the Markdown file or current directory, load user TOML from `XDG_CONFIG_HOME` or `~/.config/poddown/config.toml`, map validation failures to stderr and code 2, and keep all provider/network/storage options out of this slice.

- [x] **Step 4: Add module execution and console entry point.** `src/poddown/__main__.py` must call `poddown.cli.main`; add `[project.scripts] poddown = "poddown.cli:main"` to `pyproject.toml` without changing runtime dependencies.

- [x] **Step 5: Run the focused green gate.** Run the BDD, unit, and subprocess tests from Task 1. Expected result: all scenarios and focused tests pass, with no provider/network fixture enabled.

### Task 3: Verify, document, review, and publish the slice

**Files:**
- Create: `docs/verification/preview-cli.md`
- Modify: `docs/planning-traceability.md`
- Modify: `docs/superpowers/plans/2026-08-10-preview-cli.md`

**Interfaces:**
- Documentation must describe the command, configuration precedence, exit code 2 validation behavior, JSON shape, and explicit no-provider limitation.
- Traceability must link the new BDD feature, unit/integration tests, and verification record to spec 005.

- [x] **Step 1: Run the full changed-scope gate.** Run `make check` after docs and code are final; do not run `make check-full` or `make quality-gates` locally.

- [x] **Step 2: Run applicable packaging and safety checks.** Run `make build`, `make docs`, `uv lock --check`, `uv pip check`, `python -m compileall -q src tests`, schema/JSON parsing, credential audit, and `git diff --check`. Remove only exact generated coverage/cache files.

- [x] **Step 3: Request an independent Luna/Terra review.** Review the source-bound hash, config precedence, malformed UTF-8/frontmatter behavior, stable JSON, exit codes, and proof that no provider or network boundary is called. Fix every valid Critical or Important finding with a regression test.

- [ ] **Step 4: Re-run verification after review fixes and create a normal ready PR** stacked on the current `codex/m2-package-generation` branch. Include the parent PR link, spec reference, acceptance behavior, no-provider demo command, exact test/build evidence, limitations, and rollback note.

## Deferrals

This plan intentionally does not implement asynchronous render submission, job status, publishing, package download, authentication, API clients, GitHub Actions, MCP, Temporal, database persistence, object storage, or live provider calls. Those remain separate milestones with their own contracts and BDD plans.
