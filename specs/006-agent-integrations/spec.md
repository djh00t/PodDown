# Specification: Agent Integrations

**Status:** Planned

## Goal

Expose PodDown safely to compatible agents through capability (MCP) and instruction
(skill) without duplicating business rules.

## MCP contract

Tools are `poddown_preview`, `poddown_render`, `poddown_publish`,
`poddown_get_status` and `poddown_get_episode`. Inputs and outputs reuse public API
schemas. Tool descriptions distinguish free validation/preview, paid asynchronous
rendering and externally visible publishing. Publish requires a scoped authorization
and explicit user approval; the MCP server cannot infer it from prior render consent.

Resources may expose redacted episode status, transcript and manifest. Audio uses
authorized resource links rather than inline payloads. Tenant identity comes from
authenticated server context, never a model-supplied tenant ID.

## PodDown skill

The published skill teaches source-bound Markdown preparation, audio-friendly
structure, narration/dialogue selection, pronunciation hints, profiles, preview,
MCP use, QA interpretation and publish approval. It prohibits unsupported claims,
unauthorized voice cloning and silent publication. A versioned eval suite tests
tool choice, source preservation, refusal and approval boundaries.

A future ChatGPT app remains tool-first; add a widget only when waveform preview,
segment approval or status materially improves the workflow.

## Acceptance behavior

1. Agents choose preview before render when validation or adaptation is uncertain.
2. Tool schemas cannot inject tenant scope or weaken QA/rights gates.
3. Publish is rejected without a fresh scoped approval.
4. Skill evals preserve facts and correctly distinguish all tool side effects.
5. MCP failures return stable safe errors without source, voice IDs or credentials.

## Production-closure authentication and evidence contract

MCP has explicit `local` and `api` modes; API mode is the production default and
delegates preview, render, status, publish and resource access to the authenticated
FastAPI service. An `AuthenticatedPrincipal` supplies verified issuer, subject,
tenant, project scope and scopes. Tenant/project headers are accepted only in local
auth mode. Publication consumes a durable, one-time, scoped `PublicationApproval`
whose operation, actor, nonce hash, issue/expiry and consumption timestamps are
audited. MCP output labels deterministic, host-local and live-provider evidence and
never elevates local evidence to live fidelity.
