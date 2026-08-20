# Provider evidence boundary verification

Provider evidence records operation, provider request identity, model, input and
output hashes, normalized usage, currency, estimated/reconciled cost, latency,
retry count, timestamp, and evidence kind. Secret values never enter records or
workflow payloads.

The execution taxonomy remains fail closed:

- deterministic-local uses synthetic bytes and script-derived transcription;
- host-local uses system TTS and host-local provenance;
- live-provider requires provider response audio, provider ASR, complete
  provider metadata/cost, consent, route policy, and 100% critical-token QA.

The current suite covers provider policy, evidence validation, adaptation
envelopes, replay/conflict handling, and durable local recorder boundaries. No
credentialed ElevenLabs/OpenAI request or provider-ASR fidelity result exists;
injected transports and deterministic transcripts are not live evidence.
