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

## Production-closure durability contract

Publication attempts and receipts are durable PostgreSQL records scoped by tenant,
project, target and idempotency key. S3 publication uses the content-addressed object
store and verifies SHA-256, byte count, media type and schema metadata on write and
read. Identical concurrent puts are idempotent; mismatched existing bytes fail closed.
Transistor calls use an injected transport and recorded contract fixtures by default;
live mutations require explicit opt-in and a fresh approval. External publishing
accepts only packages whose evidence record is `live_eligible`, while filesystem and
MinIO demos may accept explicitly labelled local evidence.
Durable publication approvals carry a nonblank actor identifier and a lowercase
SHA-256 nonce hash. Approval consumption matches tenant, project, episode,
publication, operation, actor and nonce, and succeeds at most once before expiry.
Migration 012 adds these fields with `NOT VALID` checks so legacy rows can be
backfilled and then validated before a later `NOT NULL` enforcement step.
