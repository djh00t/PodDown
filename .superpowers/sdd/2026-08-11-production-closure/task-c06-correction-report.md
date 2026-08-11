# C06 API contract correction report

## Scope

Align the render request and episode status models with the frozen production-closure wire contract while preserving compatibility for existing in-process callers.

## TDD evidence

- RED: after contract tests were updated, `rtk uv run pytest -q tests/unit/test_api_models.py` reported 4 failures because `mode`, `max_cost`, `episode_version_id`, and `package_manifest_sha256` were not accepted by the existing models.
- GREEN: `rtk uv run pytest -q tests/unit/test_api_models.py tests/bdd/test_api_contract_models.py tests/integration/test_episode_api.py` — 56 passed, 6 upstream pytest-bdd deprecation warnings.

## Contract evidence

- Render JSON properties are exactly `provider_route_id`, `mode`, and `max_cost`.
- `max_cost` is represented as a decimal string in JSON and numeric JSON is rejected.
- Legacy input aliases `execution_mode`, `cost_ceiling`, and UUID route objects remain accepted only at the in-process boundary; serialization emits the frozen names.
- Status exposes `episode_version_id`, the frozen lifecycle-stage enum, `package_manifest_sha256`, UUIDv7 publication identity, bounded progress, and allowlisted failure data.
- Legacy service lifecycle states are explicitly mapped to the frozen public stage names.

## Quality evidence

- Ruff format check: pass.
- Ruff check: pass.
- Strict mypy for owned API modules: pass.
- `git diff --check`: pass.

## Boundary

No live providers, credentials, external services, or publication targets were used. The pre-durable local repository currently reuses the episode UUID for its initial version identity; durable version identity remains owned by the production persistence/workflow milestones.

## Review disposition

- The reviewer correctly identified that the current legacy dispatcher does not yet consume render controls; API-to-workflow propagation is explicitly owned by A09. This package now keeps the model contract exact and preserves the bodyless default while that dependency is pending.
- The reviewer suggested removing `episode_id` from status, but the approved contract explicitly includes `episode_id`; the exact HTTP assertion therefore retains it.
- Independent UUIDv7 and progress-bound tests were added after review.
