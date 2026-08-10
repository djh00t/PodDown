# Immutable artifact and episode-package verification

## Scope

This M2 slice adds content-addressed immutable artifact storage and a
replay-safe filesystem-backed episode-package manifest commit boundary. Exact
bytes are identified by lowercase SHA-256, stored once, and reverified on
read. The package requires the nine approved members and emits only the
episode-package schema fields, while preserving extensible provenance details.

Package commit validates UUID identity, safe member names, duplicate/missing
members, exact byte metadata, source/script/profile/renderer provenance, final
QA pass, critical-token accuracy of 1.0, and final_sha256 bound to episode.wav.
Identical commits replay the existing manifest; conflicting commits cannot
replace it.

The verification path is deterministic local filesystem storage. No external
provider, object-storage credential, or publication target is used.

## Acceptance evidence

BDD scenarios in
[package.feature](../../tests/features/package.feature) and bindings in
[test_package.py](../../tests/bdd/test_package.py) prove:

- complete nine-file commits preserve approved manifest order and exact checksums;
- missing, duplicate, and unsafe package members fail closed;
- failed QA and incomplete critical-token accuracy block commit;
- equivalent manifests serialize deterministically with schema-safe fields;
- identical replay returns the original immutable manifest;
- conflicting replay preserves the original manifest; and
- corrupted stored bytes fail read-time integrity verification.

Unit coverage in [test_artifacts.py](../../tests/unit/test_artifacts.py) and
[test_packages.py](../../tests/unit/test_packages.py) covers atomic
content-addressed writes, immutable references, path/key forgery, malformed
provenance, final checksum binding, and failure atomicity.

## Verification record

Focused package gate:

    PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
    .venv/bin/pytest -p pytest_bdd.plugin -q
    tests/bdd/test_package.py tests/unit/test_artifacts.py tests/unit/test_packages.py
    34 passed

Focused quality:

    .venv/bin/ruff format --check and ruff check passed
    .venv/bin/mypy --strict src/poddown/artifacts.py src/poddown/packages.py
    Success: no issues found in 2 source files

Changed-scope evidence: make check passed 549 tests with 1 live-provider test
deselected and 86.75% total branch coverage; Ruff and strict mypy passed across
37 source files. make build, make docs, uv lock --check, uv pip check,
compileall, episode-package schema-field validation, credential audit, and
git diff --check all passed. No live provider or external storage service was
used.

## Deferrals

Temporal idempotency, object-storage adapters, provider billing reconciliation,
publishing, and package generation from live final-master artifacts remain
follow-up milestones. This slice supplies the immutable contracts and local
adapter those milestones will consume.
