# Specification: CLI and GitHub Automation

**Status:** Planned

## Goal

Make PodDown simple for people and deterministic for CI through one thin client of
the public API and contracts.

## CLI contract

`poddown preview <markdown>`, `render`, `publish` and `status <job-id>` support
profile selection, API endpoint, output directory and `--json`. Preview validates,
resolves configuration and optionally adapts a script without paid voice rendering.
Render submits asynchronously and may `--wait`; publish requires explicit
confirmation unless a CI approval token/policy is supplied. Exit codes distinguish
validation, authentication, workflow failure and network failure.

Configuration precedence is flags, document frontmatter, project config, user
config, then defaults. Secrets come only from environment or secret providers.
Downloaded episode packages are checksum verified and atomically installed.

## GitHub Action

The action validates Markdown on pull requests and can render on an approved merge,
tag or manual dispatch. It uses OIDC or repository secrets, concurrency keys prevent
duplicate renders, job summaries link to status/package, and untrusted forks never
receive secrets or execute live provider calls. Publishing is a separate protected
job/environment.

## Acceptance behavior

1. All four commands work interactively and with stable JSON output.
2. Preview performs zero paid renderer calls.
3. Ctrl-C during `--wait` does not cancel the durable remote job.
4. Repeated CI runs reuse the idempotent episode version/render.
5. Untrusted pull requests cannot access credentials or publish.
6. A downloaded package with a checksum mismatch is rejected.

