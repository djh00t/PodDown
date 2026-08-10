# Offline preview CLI verification

## Scope

This slice adds the zero-cost `poddown preview` command for validating a local
Markdown source before any render work. It reads source bytes once, validates
PodDown frontmatter, resolves a local profile name using flag, document,
project, user, and default precedence, and reports a stable SHA-256 digest,
source size, content block count, and `provider_calls: 0`.

The command does not import or call a renderer, provider, network service,
storage service, package generator, or publication adapter. Invalid UTF-8,
frontmatter, TOML, and profile configuration fail with exit code `2` and no
success output.

## Usage

```bash
python -m poddown preview episode.md
python -m poddown preview episode.md --config poddown.toml --json
```

The optional `--profile` flag has highest precedence. Without it, the command
uses the document's `poddown.profile`, explicit project configuration, the
user configuration at `$XDG_CONFIG_HOME/poddown/config.toml` (or
`~/.config/poddown/config.toml`), and finally `default`.

JSON output is compact, sorted, UTF-8, and deterministic:

```json
{"block_count":2,"profile_id":"default","provider_calls":0,"source_bytes":17,"source_sha256":"..."}
```

## Acceptance evidence

The BDD contract in [preview.feature](../../tests/features/preview.feature)
and [test_preview.py](../../tests/bdd/test_preview.py) covers valid summaries,
stable JSON, exact source hashing, zero provider calls, invalid frontmatter,
unknown profiles, and precedence.

Unit and subprocess coverage is in
[tests/unit/test_preview.py](../../tests/unit/test_preview.py) and
[tests/integration/test_preview_cli.py](../../tests/integration/test_preview_cli.py).

Focused contract gate:

```text
uv run pytest -q tests/unit/test_preview.py tests/bdd/test_preview.py tests/integration/test_preview_cli.py
24 passed
```

The intake regression gate also passes:

```text
uv run pytest -q tests/bdd/test_intake.py tests/unit/test_intake.py tests/unit/content/test_source.py tests/unit/content/test_profiles.py
65 passed
```

Changed-scope repository gate:

```text
make check
618 passed, 1 live-provider test deselected
coverage 86.40% (required 80.0%)
ruff format --check: 98 files already formatted
ruff check: passed
mypy: passed for 41 source files
```

Additional safety checks passed: `make build`, `make docs`, `uv lock --check`,
`uv pip check`, Python compilation, `git diff --check`, and a credential
pattern scan over changed source, tests, and documentation.

## Limitations and deferrals

Preview is intentionally local and read-only. It does not render audio,
submit jobs, contact providers, persist artifacts, publish episodes, or expose
an API, MCP server, GitHub Action, Temporal workflow, or live-provider mode.
