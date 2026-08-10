# CLI and GitHub automation verification

## Scope

This slice adds the provider-free `poddown` command surface for preview,
render, publish, and status operations, including stable JSON output, exit-code
mapping, configuration precedence, resumable status polling, explicit publish
authorization, and checksum-safe package installation. It also adds a local
GitHub Action and pull-request validation workflow that exercises preview only.

The CLI uses the existing HTTP API contract and environment variables for
tenant/project context and secrets. Live provider rendering, external
publication, hosted credentials, and GitHub write operations remain explicitly
deferred from this local deterministic slice.

## TDD evidence

The contract tests were written before the production module existed:

```text
uv run pytest -q tests/unit/test_cli.py tests/integration/test_cli.py \
  tests/bdd/test_cli.py
ImportError: cannot import name 'cli' from poddown
```

After implementation, the same focused selection passed **18 tests**.

## Verification commands and results

```bash
make check
```

Result: **748 passed, 1 live-provider test deselected**, with no resource
warnings. Branch-aware total coverage was **86.15%** against the repository's
80% threshold. Ruff formatting/linting and strict mypy passed.

The suite covers preview without provider dispatch, endpoint and
frontmatter/project/user configuration precedence, stable JSON and exit codes,
API render/status polling, Ctrl-C without cancellation, publish authorization,
the `package-install` checksum gate, atomic package installation, and workflow
trust boundaries. The workflow has separate protected render and publication
jobs with concurrency keys and job summaries; pull-request preview receives no
credentials.

Additional checks passed:

```bash
make build
make docs
uv lock --check
uv pip check
uv run python -m compileall -q src tests
git diff --check
```

The credential scan found no matches. Tests use local fixtures and fake HTTP
transports; no provider, network, credential, or publication spend is invoked.
