# PodDown Constitution

## Core Principles

### I. Markdown Is Canonical

PodDown accepts ordinary Markdown with optional YAML frontmatter. The source
document remains useful without PodDown and is the authority for every factual
claim. Reusable behavior belongs in versioned show profiles, not a proprietary
Markdown dialect.

### II. Source-Bound Production

PodDown owns adaptation and script generation. Voice providers render approved
segments and must not invent episode content. Every generated factual statement
must be supported by the source package. Critical factual tokens require 100%
fidelity; a mismatch fails the segment.

### III. Quality Is a Release Gate

An audio file is not a successful result by itself. Each render must pass
transcription comparison, critical-token verification, pronunciation checks,
audio diagnostics, and a final post-master verification. Failed segments are
re-rendered independently. The listener-quality target is audio worth subscribing
to consistently.

### IV. Rights, Transparency, and Tenant Isolation

Rendering requires a valid, auditable rights record for every voice asset. PodDown
must not support unauthorized voice cloning or falsely imply that synthetic
presenters are human employees. Every tenant-owned record and artifact is tenant
scoped, and disclosure is configurable by project and publishing jurisdiction.

### V. Reproducibility and Provenance

Every episode version is immutable once published and traceable to source,
script, profile, lexicon, provider model, voice asset, mastering profile, QA
results, costs, and final checksum. Learning may create new versions but must
never silently alter a published version.

### VI. Durable, Provider-Neutral Workflows

Long-running work executes as resumable, idempotent Temporal workflows. Provider,
publishing, storage, and transcription integrations sit behind explicit ports.
Business rules never depend on one vendor. NATS JetStream carries integration
events, not workflow correctness.

### VII. Behavior-First, Test-Driven, Minimal Delivery

Work follows Spec-Kit specifications, plans, tasks, BDD, TDD, and verification
before completion. Every externally observable requirement begins as a reviewed
Gherkin scenario and executable `pytest-bdd` test. Each implementation increment
then follows red-green-refactor: run the relevant scenario and observe the
expected failure before writing production code. Prefer the smallest coherent
vertical slice, modular Python, DRY interfaces, and no TODO placeholders. Core
behavior requires at least 80% line coverage plus behavioral evals for fidelity
and audio quality.

## Engineering Standards

- Python, FastAPI, PostgreSQL, Temporal, NATS JetStream, S3-compatible storage,
  ffmpeg, uv, pytest, pytest-bdd, pytest-cov, and Docker Compose are the initial
  stack.
- Code follows PEP 8 and Google-style documentation conventions.
- Configuration enters through validated environment variables and profiles.
- Conventional Commits, Semantic Versioning, semantic-release, and GitHub Actions
  govern change and release management.
- The Makefile exposes `clean`, `install`, `build`, `test`, `lint`, and `docs`.
- Kubernetes, billing, team administration, and complex UI are excluded until an
  operational or validated product need exists.

## Delivery Gates

A change may merge only when its specification and acceptance criteria are
traceable to tests, tests and static checks pass, generated schemas remain
backward compatible or are explicitly versioned, and no unresolved critical QA,
rights, security, or provenance failure remains.

## Governance

This constitution overrides conflicting implementation convenience. Amendments
require a documented rationale, migration impact, and version change. Compliance
is reviewed during specification, plan, code review, and release verification.

**Version:** 1.1.0
**Ratified:** 2026-08-09
**Last amended:** 2026-08-09
