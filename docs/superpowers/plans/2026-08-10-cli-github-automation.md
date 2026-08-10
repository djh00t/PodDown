# M4 CLI and GitHub Automation Plan

> Execute on `codex/m4-cli-automation`, directly stacked on PR #19
> (`codex/m3-durable-persistence`). Keep the CLI a thin public-contract client
> and keep GitHub validation safe for untrusted pull requests.

## Objective

Deliver the first usable PodDown developer experience: `preview`, `render`,
`publish`, and `status` commands with stable JSON/exit contracts, configuration
precedence, checksum-safe package installation, and a GitHub validation workflow.
Preview must remain provider-free and `--wait` must never cancel a remote job.

## Scope and ownership

Owned files:

- `src/poddown/cli.py`
- `pyproject.toml` only for the `poddown` console entry point
- `tests/features/cli.feature`
- `tests/bdd/test_cli.py`
- `tests/unit/test_cli.py`
- `tests/integration/test_cli.py`
- `.github/actions/poddown-preview/action.yml`
- `.github/workflows/poddown-validate.yaml`
- `docs/verification/cli-github-automation.md`
- this plan

Shared coordinator file:

- `docs/planning-traceability.md`

No API route, provider adapter, persistence, object-storage, or audio module is
rewritten. Network behavior is isolated behind a small standard-library HTTP
port and is mocked in offline tests.

## Contract decisions

- `poddown preview <markdown>` reads the source, resolves profile/configuration,
  validates Markdown/frontmatter, and emits deterministic human or sorted JSON
  output without dispatching a renderer or making a network call.
- `render`, `publish`, and `status` use the public HTTP contract with required
  tenant/project context from explicit flags or environment-backed configuration.
  They send idempotency keys and map validation, authentication, workflow, and
  network failures to stable exit codes.
- `--wait` polls status until terminal state. KeyboardInterrupt exits with a
  distinct interrupted code and does not send cancellation or deletion requests.
- Configuration precedence is flags, document frontmatter, project
  `.poddown.yaml`, user config, then safe defaults. Secrets are read only from
  environment variables; they are never accepted in config files or printed.
- Verified package installation writes to a temporary sibling, checks the
  expected SHA-256, and atomically replaces the destination only after success.
- The GitHub pull-request workflow runs preview validation with no secrets. Any
  render path is manual/approved only, uses concurrency keys, and keeps
  publication outside the untrusted validation job.

## Acceptance behaviors

1. All four commands expose stable JSON and documented exit codes.
2. Preview validates and resolves configuration with zero HTTP/provider calls.
3. Render submits asynchronously and repeated idempotency keys are reused.
4. Publish requires explicit confirmation or a CI approval token/policy.
5. Status and `--wait` report terminal outcomes; Ctrl-C leaves the remote job
   untouched.
6. Package installation rejects checksum mismatch and does not partially write.
7. Pull-request automation runs preview without credentials; protected render
   remains separate and cannot run for untrusted forks.

## TDD execution order

### Task 1: Write BDD, unit, integration, and workflow-contract tests first

- Add BDD scenarios for provider-free preview, config precedence, stable JSON,
  asynchronous render, publish authorization, status polling interruption,
  checksum-safe installation, and untrusted PR validation.
- Add unit tests for exit mapping, config resolution, HTTP request construction,
  JSON ordering, YAML/action schema, and atomic package verification.
- Add integration tests with a fake HTTP server/transport and temporary files.
- Capture RED before the CLI module and console entry point exist.

### Task 2: Implement the CLI and safe package installation

- Use `argparse`, existing Markdown validation/profile parsing, and standard
  library HTTP/file primitives; do not add a web or CLI framework dependency.
- Keep preview paths provider-free and separate network transport from command
  formatting.
- Add the console script and concise operational documentation.

### Task 3: Add GitHub validation automation and verify

- Add a local action/workflow that validates Markdown on pull requests without
  secrets, and a manually approved render path with concurrency isolation.
- Run focused BDD/unit/integration tests, existing regressions, changed-scope
  `make check`, build, docs, lock, dependency, compile, YAML, credential, and
  clean-diff checks.
- Request independent Terra/Luna review for exit contracts, secret boundaries,
  Ctrl-C behavior, atomic installation, and workflow trust boundaries.
- Fix every valid Critical, Important, or acceptance-blocking finding before
  creating a normal ready PR stacked on PR #19.

## Explicit deferrals

Live provider rendering, package publication, OIDC token exchange, complex
interactive UI, billing, and protected production deployment remain later
milestones. The workflow must not imply that deterministic preview is a live
provider or publication demonstration.
