# Live-provider execution runbook

This runbook is the controlled path for producing live ElevenLabs audio,
OpenAI transcription, and source-bound reasoning evidence. It is intentionally
separate from the deterministic-local and host-local reference demos. A key in
the environment is not authorization to spend money or to use a voice.

## Required approvals before activation

Record all of the following in the immutable execution review bundle before
setting `PODDOWN_PROVIDER_LIVE_ENABLED=1`:

- tenant, project, episode, source, and profile identifiers;
- approved ElevenLabs voice mapping and rights/consent evidence for every voice;
- provider route, model, endpoint, pricing version, request/episode ceilings,
  and a maximum approved spend;
- approval for OpenAI transcription/reasoning and the expected data-retention
  policy;
- operator, timestamp, change/review identifier, and rollback owner.

Do not put secret values, raw source text, audio, transcripts, or provider
payloads in environment dumps, workflow payloads, logs, or review comments.

## Secret-free runtime configuration

The live route must use `env://` secret references in
`PODDOWN_PROVIDER_ROUTE_JSON`; the corresponding environment variables are
provisioned by the runtime secret manager and are never persisted in evidence.
At minimum, the route must identify ElevenLabs as renderer and OpenAI as
transcriber, use an approved live model/capability set, and contain positive
request and episode cost ceilings.

The worker/API configuration also requires:

```text
PODDOWN_WORKFLOW_MODE=live-provider
PODDOWN_WORKFLOW_FIXTURE_ROOT=<approved fixture or production content root>
PODDOWN_PROVIDER_LIVE_ENABLED=1
PODDOWN_PROVIDER_ROUTE_JSON=<secret-free route JSON>
PODDOWN_PROVIDER_ENDPOINT=<approved HTTPS provider endpoint>
PODDOWN_PROVIDER_TIMEOUT_SECONDS=<bounded timeout>
PODDOWN_LIVE_VOICE_MAP_JSON=<local voice asset to provider voice mapping>
PODDOWN_LIVE_CONSENT_JSON=<provider voice to consent-evidence mapping>
PODDOWN_ELEVENLABS_COST_PER_CHARACTER=<approved pricing input>
PODDOWN_ELEVENLABS_ZERO_RETENTION=0|1
PODDOWN_OPENAI_COST_PER_AUDIO_SECOND=<approved pricing input>
PODDOWN_OPENAI_REASONING_SELECTION_JSON=<secret-free R09 selection.to_record() JSON>
PODDOWN_OPENAI_COST_PER_REASONING_INPUT_TOKEN=<approved pricing input>
PODDOWN_OPENAI_COST_PER_REASONING_OUTPUT_TOKEN=<approved pricing input>
PODDOWN_LIVE_RENDER_ESTIMATED_COST=<bounded preflight estimate>
PODDOWN_LIVE_TRANSCRIPTION_ESTIMATED_COST=<bounded preflight estimate>
PODDOWN_POSTGRES_DSN=<secret-managed durable database reference>
PODDOWN_TEMPORAL_ADDRESS=<approved Temporal endpoint>
PODDOWN_TEMPORAL_NAMESPACE=<approved namespace>
PODDOWN_TEMPORAL_TASK_QUEUE=<approved task queue>
```

The bounded one-segment smoke harness additionally requires a separate explicit
test opt-in and operator-supplied, non-sensitive synthetic text:

```text
PODDOWN_LIVE_PROVIDER_TESTS=1
PODDOWN_LIVE_SMOKE=1
PODDOWN_LIVE_SMOKE_VOICE_ASSET_ID=<approved local voice asset>
PODDOWN_LIVE_SMOKE_TEXT=<approved synthetic segment text>
PODDOWN_LIVE_SMOKE_CRITICAL_TOKENS_JSON=["<approved token>"]
```

Run it only after the approvals above, with the live marker selected:

```bash
uv run pytest -m live_provider tests/live/test_live_provider_smoke.py -q
```

Without both opt-in variables, the smoke is skipped and makes no provider
request. It performs exactly one ElevenLabs render and one OpenAI transcription,
uses the durable candidate ledger, validates provider identities/checksums/cost
ceilings, and requires 100% critical-token fidelity. The harness is evidence
collection support; a skipped run is not live-provider evidence.

The secret references in the route must resolve to provisioned provider
credentials such as `env://ELEVENLABS_API_KEY` and
`env://OPENAI_API_KEY`. Never paste those values into a route, command, test,
report, or source file.

`PODDOWN_OPENAI_REASONING_SELECTION_JSON` is the canonical runtime selection
record produced by the deterministic R09 source-fidelity evaluation. The
worker uses its selected model for the OpenAI Responses transport and rejects
malformed provenance. `PODDOWN_OPENAI_REASONING_MODEL` may be supplied as a
compatibility assertion, but if present it must exactly match the selected
model; it is not an independent selection mechanism.

## Execution and evidence gates

1. Run the configuration/preflight checks with live execution still disabled.
2. Verify the route, voice consent, cost ceilings, tenant scope, immutable
   source snapshot, and provider secret references.
3. Obtain the explicit spend/voice approval, then enable live execution for the
   single approved episode only.
4. Execute through the API/Temporal workflow; do not call provider clients
   directly or silently fall back to host-local speech.
5. Require provider-response audio, provider-ASR transcription, complete
   provider request/model/usage/cost/latency/retry evidence, and 100% critical-
   token accuracy before packaging is considered eligible.
6. Reconcile estimated and provider-reported cost in PostgreSQL before marking
   the episode complete.
7. Preserve failed-segment-only rerender evidence and the exact package,
   manifest, transcript, provenance, and publication decision digests.

Any missing provider response, ASR evidence, consent, cost reconciliation, or
critical-token match is a terminal non-live result. There is no fallback from a
live-provider failure to host-local or deterministic output.

## Current status

The repository contains the fail-closed adapters, route validation, provider
evidence, usage ledger, and local/injected tests for this procedure. No
credentialed provider request has been made from the current workspace, and no
live-provider readiness claim is established until the approvals and evidence
above exist.
