# Local release-evidence verification

The release boundary is fail closed and requires evidence for tests, lock state,
SBOM, vulnerability scan, signature, and staged deployment. A missing or
skipped external check keeps a candidate ineligible.

The current local contract slice passed:

```text
tests/bdd/test_sbom.py
tests/bdd/test_release_evidence.py
tests/unit/test_release.py
11 passed in 2.07s
```

Previously captured provider-free scan evidence for the current reconciled
overlay:

- `make build` produced the source distribution and wheel successfully; Python
  `compileall` and `git diff --check` also passed.
- `scripts/generate_sbom.py` produced CycloneDX 1.5 with 60 locked components;
  SHA-256 `4bcfacd2fd7d0272d27f80c6aa2d0ec8c2d529a5f7d1b9ae0ae7bb9be2913be4`.
- Gitleaks scanned approximately 35.20 MB and reported no leaks; report
  SHA-256 `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570`.
- Trivy library scanning exited `0` with no unfixed findings; the empty report
  SHA-256 is
  `74234e98afe7498fb5daf1f36ac2d78acc339464f950703b8c019892f982b90b`.

These are local scanner and contract results only. No signing key, signed
artifact, staged deployment, credential rotation, hosted service, or
authenticated release environment was used. Production release readiness is
therefore not established.

The fresh `uv lock --check` and `uv pip check --python
/Users/djh/PodDown-main-clean-reconcile/.venv/bin/python` both passed during the
current verification pass. A fresh `uv build` was not run in this pass; the
previous `make build` result above remains the available build evidence.

The 2026-08-16 local Compose verification also exercised API-to-Temporal
host-local package completion across PostgreSQL, Temporal, NATS, MinIO, API,
and worker containers. A second disposable run restarted API and worker during
rendering and recovered to package completion; its canonical manifest digest
was `32a9ebebbb342b97cdd7c7a837d0a36d6482af6bf52c76bff28c15c85765472b`, and
the API status projection matched it. This is local integration evidence only;
it is not a signed artifact, staged deployment, hosted recovery drill,
provider-live run, or authenticated release UAT.
