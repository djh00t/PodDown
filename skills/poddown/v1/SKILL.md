---
name: poddown
version: 1.0.0
description: Source-bound, approval-aware PodDown agent workflow.
---

# PodDown agent skill v1

Use `poddown_preview` before `poddown_render` whenever source validity,
adaptation, profile choice, or preservation is uncertain. Preview is local and
free; render is asynchronous and may consume paid provider capacity.

Preserve the user's Markdown facts and source bytes. Do not invent claims,
silently rewrite source, request unauthorized voice cloning, or expose source,
credentials, provider payloads, or voice identifiers in responses.

Use `poddown_get_status` and `poddown_get_episode` for redacted progress and
authorized resource links. Audio must remain a resource link, never inline data.

Use `poddown_publish` only after the user gives fresh, episode-scoped approval.
Render consent is not publish approval. Refuse publication when approval is
missing, stale, cross-tenant, or ambiguous, and explain the safe next step.

The authenticated server supplies tenant scope. Never add a tenant identifier
to tool arguments or infer one from source content.
