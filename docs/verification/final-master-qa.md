# Final-master transcription and fidelity QA verification

## Scope

This M2 slice adds an independent QA boundary over a verified MasteredAudio.
It rechecks the exact mastered WAV bytes against the versioned
MasteringProfile, rejects malformed or out-of-profile media before provider
dispatch, transcribes the exact bytes through the asynchronous Transcriber
port, and binds the returned checksum to those bytes.

Critical-token fidelity uses the existing deterministic verifier, including
repeated occurrences and negation handling. The typed result retains transcript
provider usage, request, checksum, and decimal cost evidence while to_dict()
emits only the versioned QA schema fields for a stage=master record. Failed
fidelity is a failed QA record; malformed media, checksum mismatch, and
terminal provider evidence are terminal errors. Provider timeout and rate-limit
failures are retryable errors.

The default verification path is deterministic local testing with injected
transcriber fakes. It does not claim a live provider call, live billing, or
package publication.

## Acceptance evidence

BDD scenarios in
[final_master_qa.feature](../../tests/features/final_master_qa.feature) and
bindings in
[test_final_master_qa.py](../../tests/bdd/test_final_master_qa.py) prove:

- exact final-master bytes are dispatched once and checksum-bound;
- missing critical tokens produce a failed stage=master record;
- transcript checksums for different bytes fail closed;
- malformed media is rejected before transcription dispatch;
- timeouts and rate limits map to retryable errors while terminal provider
  failures map to non-retryable errors; and
- equivalent master evidence serializes deterministically with only
  schema-compatible fields.

Unit coverage in
[test_final_master.py](../../tests/unit/qa/test_final_master.py) covers
immutable gate evidence, profile/media preflight, provider usage and cost,
malformed transcript evidence, and stable result serialization.

## Verification record

Focused final-master gate:

    PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
    .venv/bin/pytest -p pytest_bdd.plugin -q
    tests/bdd/test_final_master_qa.py tests/unit/qa/test_final_master.py
    19 passed

Focused quality checks:

    .venv/bin/ruff check
    src/poddown/qa/final_master.py
    tests/bdd/test_final_master_qa.py
    tests/unit/qa/test_final_master.py
    All checks passed

    .venv/bin/mypy --strict src/poddown/qa/final_master.py
    Success: no issues found in 1 source file

The complete changed-scope repository gate, audio regressions, build,
documentation build, schema validation, dependency audit, security audit,
and clean-diff results are recorded here before the stacked PR handoff.
Changed-scope evidence: make check passed 511 tests with 1 live-provider
test deselected and 87.22% total branch coverage; Ruff and strict mypy
passed across 35 source files. make build, make docs, uv lock --check,
uv pip check, compileall, schema-field validation, credential audit, and
git diff --check all passed. No live provider credentials or external
provider calls were used.

## Provenance and deferrals

The result retains exact master checksum, normalized transcript, provider
usage, request ID, mode, and decimal cost for the subsequent package and
provenance boundary. The schema record intentionally excludes provider details
and raw transcript text.

This slice does not yet assemble the immutable nine-file episode package,
generate transcripts/VTT/chapters/show notes, rerender failed segments,
publish media, reconcile provider invoices, or demonstrate a live provider.
Those remain dependency-safe follow-up milestones.
