# Specification: Publishing

**Status:** Planned

## Goal

Publish one immutable, verified episode package through replaceable adapters with
explicit authorization and an auditable result.

## Contract

`Publisher.publish(package, target, authorization, idempotency_key)` returns an
immutable `PublicationReceipt`. Adapters initially cover filesystem,
S3-compatible object storage, generic RSS and Transistor. Provider payloads do not
cross the port. Publication references package checksums and cannot alter audio,
transcript, chapters, notes or provenance.

Targets define credentials by secret reference, show/feed mapping, disclosure
policy, visibility and update policy. Spoken disclosure, show-note disclosure and
platform metadata are resolved before mastering/package finalization when required.
Revocation prevents new publication but does not silently delete existing media.

## Acceptance behavior

1. Only QA-passed immutable packages can publish.
2. Repeated calls with one idempotency key return one external episode.
3. Partial provider failure resumes or compensates without duplicate feed entries.
4. Filesystem/S3 outputs exactly match package checksums.
5. RSS is valid and deterministic; Transistor contract tests use recorded fixtures.
6. Authorization, disclosure and publication receipt are captured in provenance.
7. Updating or deleting a publication is an explicit separately authorized action.

