# O12–O14 release evidence verification

## Scope

The release boundary now requires a semantic version, source commit, lockfile
checksum, artifact checksums, and explicit results for tests, lock validation,
SBOM generation, vulnerability scanning, signing, and staged deployment.

Any failed check or skipped external evidence makes the candidate ineligible.
The evidence record contains no source, audio, provider payload, or credential
values.

## Local evidence

```text
PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  pytest -p pytest_bdd.plugin -q \
  tests/bdd/test_release_evidence.py tests/unit/test_release.py
10 passed
```

The latest repository-wide non-live verification also passed **1560 tests**,
with **1** live-provider test deselected and **6** warnings, in 27:14, at
**80.22% branch coverage**. This does
not include live provider, hosted service, authenticated UAT, signing, or
staged-release evidence.

## Evidence boundary

The gate validates evidence supplied by build/security/release systems. It does
not sign artifacts or deploy a staging environment, and its contract does not
replace a complete security scan. Those checks remain required before release
readiness can be claimed.

The repository now provides a deterministic lockfile SBOM generator and Make
target. Its local regression passed **1 test**; a generated CycloneDX 1.5
artifact contained **61** locked library components and was hashed as
`7ef9c4ae78776b7c196a68178e611fc307b91e75479a97b2b29c9b30c75946e1`.

```bash
make sbom SBOM_OUTPUT=/tmp/poddown-sbom.cdx.json
```

## Current local scan attempt

On 2026-08-15, the installed Trivy filesystem scanner completed a database-backed
library scan with exit code 0 and reported no unfixed library vulnerabilities:

```bash
cd /path/to/PodDown
trivy filesystem --vuln-type library --format json \
  --ignore-unfixed --skip-dirs .git --skip-dirs .venv --skip-dirs .cache \
  .
```

The fresh rerun exited `0` after updating its database; the JSON report
contained zero result entries, zero vulnerabilities, and zero unfixed findings.

This is local scanner evidence only. The installed build does not expose the
secret-scanning flag. A separate Gitleaks repository scan completed with exit
code 0 and reported no leaks:

```bash
gitleaks dir . --redact --no-banner --report-format json \
  --report-path /tmp/poddown-gitleaks.json --exit-code 1
```

The fresh 2026-08-15 scan initially identified one generic-api-key false
positive in an API/Temporal test idempotency value. The value was shortened to
an unambiguously non-secret test token; the affected BDD integration then passed
(`1 passed in 30.88s`) and the rerun scanned 10.75 MB with **no leaks**.

This is local scanner evidence only; it does not replace credential rotation,
signing, staged deployment, or authenticated release evidence. No signature,
staged deployment, or authenticated release environment was produced. The
SBOM artifact was written to `/tmp` for this verification and is not a signed
release artifact.
