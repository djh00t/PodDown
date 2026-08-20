# AI Provider Integration Guidelines

## Contract ownership

PodDown owns the source-bound script, speaker assignment, pronunciation,
segmentation, rights decision, candidate selection, QA, mastering, metering, and
provenance. Providers perform bounded rendering or transcription operations.
Vendor-specific SDK objects and configuration never cross the adapter boundary.

## Required adapter behavior

Each adapter publishes a capability descriptor and validates a request against it
before incurring cost. Capabilities include models, voice controls, accepted text
and audio limits, output formats, sample rates, timestamp support, model/voice
pinning, streaming, and provider idempotency.

Each call records the PodDown idempotency key, attempt, provider request ID,
provider/model/voice references, latency, retry classification, response checksum,
input/output units, estimated and reconciled cost, and applied data policy.

Retries apply only to classified transient failures. Authentication, permission,
rights, invalid input, unsupported capability, and exhausted budget are terminal
until configuration changes. Temporal owns retry scheduling; SDK retries are
disabled or bounded and observable to prevent multiplicative retries.

## ElevenLabs rendering

- Use an explicit production model and provider voice ID resolved from an
  authorized, tenant-scoped `VoiceAsset`.
- Send one immutable segment per candidate request. Use performance controls only;
  never use provider dialogue or script generation.
- Request a lossless or highest-practical intermediate format. Validate container,
  codec, duration, channels, sample rate, non-empty PCM, and checksum.
- Pass `output_format` as the documented query parameter. The initial contract
  uses `wav_44100`; raw `pcm_44100` must not be treated as a RIFF/WAV container.
- Do not claim or send provider idempotency headers unless the endpoint documents
  them. PodDown candidate IDs still provide workflow-level deduplication.
- Treat each take as a separate billed candidate and normalize usage/cost.
- Never upload or clone a voice without a separately authorized enrollment flow
  and durable consent provenance. The vertical slice consumes pre-approved voices.

## OpenAI audio and transcription

- Keep rendering and transcription as distinct adapters with independently pinned
  models and capability descriptors.
- Rendering receives immutable expected-spoken text and may control delivery, but
  cannot add facts, remove negation, or rewrite dialogue.
- Transcription requests the richest timestamps available and normalizes text,
  words, timestamps, confidence, usage, and cost without inventing absent fields.
- Model capabilities determine response shape: `whisper-1` may request
  `verbose_json` word timestamps; GPT transcription models use `json` and expose
  no word timestamps unless their documented contract adds them.
- Retain raw responses only under project policy; persist normalized output and a
  response checksum for auditability.
- Do not trust renderer/transcriber agreement as fidelity proof. Deterministic
  critical-token comparison is authoritative, backed by labelled/cross-provider
  eval samples.

## Security, privacy, and operations

API credentials come from validated environment-backed secrets and never appear
in profiles, rows, manifests, fixtures, logs, or exception text. Logs default to
IDs and metrics. Adapters enforce provider allowlists, data policy, budget ceilings,
timeouts, concurrency, and rate limits before dispatch.

Adapters require redacted, environment-backed `ProviderSettings`; plaintext
credential strings are not accepted. Shared settings contain only the credential
and a finite bounded timeout. Provider-specific controls stay explicit:
ElevenLabs applies its data-retention choice and pre-dispatch character-cost
ceiling, while transport retries remain disabled and Temporal will own retry
scheduling. Application policy still enforces tenant eligibility and voice rights
before an adapter is constructed or dispatched.

Normal CI uses deterministic fake transports and sanitized fixtures. Live tests
require an explicit marker and environment opt-in, use non-sensitive synthetic
content and approved stock voices, enforce a small cost ceiling, and never run for
untrusted pull requests.
