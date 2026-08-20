# Provider evidence persistence verification

## Scope

The V08–V10 local slice adds a durable, tenant-scoped boundary for normalized
provider evidence and its matching usage/cost event. It preserves hashes,
provider/model/request identity, normalized units, currency, Decimal costs,
latency, retry count, timestamp, and evidence kind without persisting raw
provider payloads or credentials.

The boundary is replay-safe by (tenant_id, provider_request_id). A matching
retry returns the original pair; a changed pair fails closed. Evidence and its
usage event are inserted in one SQLite transaction, so a failed usage insert
cannot leave an orphaned evidence record.

The provider adapters also have a concrete `UrllibAsyncHttpTransport` over the
existing `AsyncHttpTransport` port. It supports deterministic JSON and
multipart request encoding, bounded timeouts, normalized response headers, and
status-preserving HTTP failures without retries or credential logging. It is
constructed only at an explicitly configured provider boundary; no runtime
default enables live provider traffic.

The durable audio activity can record render and transcription evidence through
an injected recorder. It records only first-dispatch evidence for a candidate
or transcript; activity replay uses the already persisted artifact/record.
Latency is measured as a non-negative whole-millisecond activity-side duration,
and the segment attempt contributes the workflow retry count. The preserved
worker composition now binds live reasoning, segment render/ASR, and final-master
ASR to a tenant/project/job-scoped SQLite usage ledger with operation-time
timestamps. This is local durable runtime wiring, not hosted PostgreSQL evidence.

## Acceptance evidence

BDD scenarios in
[`provider_evidence_persistence.feature`](../../tests/features/provider_evidence_persistence.feature)
prove restart replay and conflicting request rejection. Integration coverage in
[`test_provider_evidence_persistence.py`](../../tests/integration/test_provider_evidence_persistence.py)
proves atomic rollback, idempotent replay, tenant/request lookup, normalized
schema fields, and absence of raw-payload columns. Activity coverage in
[`test_provider_evidence_activity.py`](../../tests/unit/audio/test_provider_evidence_activity.py)
proves transcription and synthetic render evidence creation, hash binding,
per-episode scope binding, and no duplicate recorder call on activity replay.

The concrete transport BDD scenarios in
[`provider_http_transport.feature`](../../tests/features/provider_http_transport.feature)
passed with `2 passed`; they use a recording opener and make no network calls.

## Focused verification record

```text
PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/pytest -p pytest_bdd.plugin -q tests/bdd/test_provider_evidence.py tests/bdd/test_provider_evidence_persistence.py tests/unit/providers/test_provider_evidence.py tests/contract/providers/test_provider_evidence.py tests/unit/audio/test_provider_evidence_activity.py tests/integration/test_provider_evidence_persistence.py tests/unit/test_persistence.py tests/integration/test_durable_persistence.py tests/unit/audio/test_activities.py tests/unit/audio/test_render.py tests/unit/audio/test_storage.py
142 passed

.venv/bin/ruff check src/poddown/audio/activities.py src/poddown/audio/__init__.py src/poddown/persistence.py tests/bdd/test_provider_evidence_persistence.py tests/integration/test_provider_evidence_persistence.py tests/unit/audio/test_provider_evidence_activity.py
All checks passed

.venv/bin/mypy --strict src/poddown/audio/activities.py src/poddown/audio/__init__.py src/poddown/persistence.py tests/bdd/test_provider_evidence_persistence.py tests/integration/test_provider_evidence_persistence.py tests/unit/audio/test_provider_evidence_activity.py
Success: no issues found in 6 source files
```

## Boundaries

This is local durable evidence and injected-transport proof. It is not a live
ElevenLabs/OpenAI run, provider invoice reconciliation, PostgreSQL/RLS proof,
hosted Temporal proof, or production-readiness evidence. The current adapter
contracts expose estimated cost but not provider-reconciled cost; reconciliation
remains a later durable-data-plane responsibility.
