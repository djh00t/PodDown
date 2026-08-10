# Deterministic package-generation verification

## Scope

This slice adds a pure package-generation boundary between verified final-master
evidence and the immutable `EpisodePackageService`. It emits the nine approved
artifacts without provider calls or storage side effects, preserves exact master
audio bytes, normalizes transcript text, emits deterministic word-level VTT and
cumulative chapters, and retains source/script/profile/voice/provider/workflow,
QA, usage, cost, lexicon, and mastering provenance.

Generation fails closed for failed final-master QA, critical-token accuracy below
1.0, detached source/script/profile or content-manifest identity, missing
workflow evidence, inconsistent master or transcript checksums, malformed word
timestamps, non-JSON evidence, and invalid segment identity.

## Acceptance evidence

The BDD contract in
[package_generation.feature](../../tests/features/package_generation.feature)
and bindings in
[test_package_generation.py](../../tests/bdd/test_package_generation.py) cover:

- all nine canonical artifact names and exact deterministic audio/transcript
  payloads;
- three-decimal word-level VTT, sub-millisecond timestamp rejection, and
  cumulative segment chapters;
- source-anchored show notes without generated claims;
- compact sorted QA, provenance, and render-manifest JSON;
- final-master, critical-token, checksum, timestamp, and JSON evidence gates;
- commit and replay through `EpisodePackageService`; and
- byte-identical replay for equivalent immutable requests.

Unit coverage in
[test_package_generation.py](../../tests/unit/test_package_generation.py)
adds detached-identity, newline normalization, checksum, serialization, and
immutability regression cases.

## Verification record

Focused package-generation gate:

    rtk uv run pytest -q tests/unit/test_package_generation.py
    tests/bdd/test_package_generation.py
    35 passed

Changed-scope gate:

    rtk make check
    passed; 587 non-live tests selected, 1 live-provider test deselected;
    repository coverage and strict mypy gates passed

The final handoff also records package build, documentation build, dependency
lock, schema, compile, credential, and clean-diff checks in the delivery ledger
and pull request evidence. No live provider, paid API, object-storage service,
or publication target is used by this deterministic slice.

## Deferrals

Temporal package workflow composition, object-storage adapters, live provider
billing reconciliation, publishing, CLI/MCP invocation, and the reference demo
remain subsequent milestones. The pure builder supplies the immutable boundary
those integrations consume.
