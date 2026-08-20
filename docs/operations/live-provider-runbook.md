# Live-provider and publication runbook

This runbook is an authorization boundary. Local deterministic and host-local
demo commands do not authorize or prove live execution.

## Preconditions

Before a live run, an operator must record:

1. the exact clean-checkout commit and locked dependency digest;
2. tenant, project, source snapshot, approved route, renderer, transcriber,
   model, voice mapping, and consent identifiers;
3. provider secret references resolved by the deployment secret manager;
4. an explicit live opt-in, maximum estimated cost, and publication approval;
5. the expected critical-token ledger and the recovery owner.

No provider key or credential value belongs in a command, fixture, log, package,
or evidence record.

## Execution order

1. Run preview and verify source hash, anchors, pronunciation evidence, and the
   critical-token ledger.
2. Run the guarded one-segment live smoke with the approved route and cost
   ceiling. Confirm provider audio, provider ASR, usage, request IDs, model/voice
   metadata, and normalized cost evidence.
3. Require 100% critical-token accuracy before continuing. Any mismatch is a
   terminal QA result; do not silently fall back to host-local or deterministic
   rendering.
4. Render the full episode through the durable workflow, recording only failed
   segment recovery and replay-safe provider requests.
5. Verify the immutable package, checksums, manifest digest, rights/consent,
   disclosure, usage ledger, and recovery receipt.
6. Publish to MinIO or the explicitly approved external target only after the
   complete immutable review bundle and approval are fresh.

## Stop conditions

Stop and preserve structured failure evidence for missing consent, missing
credentials, route mismatch, budget exhaustion, provider outage, provider-ASR
token mismatch, unknown publication outcome, checksum mismatch, tenant-scope
failure, or stale approval. There is no silent live-to-local fallback.

## Evidence classification

The final report must distinguish deterministic-local structural evidence,
host-local listening evidence, live-provider audio evidence, provider-ASR
fidelity evidence, MinIO/object-storage evidence, external-publication evidence,
authenticated UAT, and deployment/recovery evidence. A successful local demo
cannot satisfy any missing live or hosted category.
