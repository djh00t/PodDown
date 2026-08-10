# Reference demo verification

The M8 reference demo is deliberately `deterministic-local-demo`. It uses
versioned source, profile, adaptation, voice-consent, and disclosure fixtures;
the local renderer, deterministic transcription fixture, local ffmpeg adapter,
SQLite ledger, filesystem package storage, and filesystem publication adapter.
It does not use a provider credential, provider spend, hosted Temporal, payment,
or external publication adapter.

The local mastering adapter requires the `ffmpeg` package, which provides both
`ffmpeg` and `ffprobe`. On macOS use `brew install ffmpeg`; on Debian/Ubuntu use
`sudo apt-get update && sudo apt-get install --no-install-recommends -y ffmpeg`.

The versioned fixtures are bundled into the wheel under
`poddown/reference-demo/v1`; the installed-wheel CLI path is part of this
verification. The demo publishes the disclosure text in `show-notes.md` and
records `spoken: false`, `show_notes: true`, and `platform: false` because no
spoken intro or external platform publication is performed.

Run it with:

```bash
uv run poddown-demo --output /tmp/poddown-reference-demo
uv run poddown-demo --output /tmp/poddown-reference-demo --resume
uv run pytest tests/bdd/test_reference_demo.py tests/unit/test_demo.py -q
```

The first command writes source/preparation/render/QA/mastering/package/usage
and publication evidence under its output directory. The package has exactly
nine artifacts. The second command replays persisted render evidence and keeps
the immutable package manifest and filesystem publication identity unchanged.
The runner exercises the authenticated `poddown_preview` MCP boundary with the
same source hash and profile ID; its result is side-effect-free.

Fresh local evidence on 2026-08-10:

- `make check`: 875 passed, 1 live-provider test deselected, 86.31% coverage;
  Ruff format/check and strict mypy passed.
- `make build` included `poddown/reference-demo/v1` and `poddown/demo.py` in the
  wheel; an isolated installed-wheel run and `--resume` run both completed.
- A fresh CLI run reported `critical_token_accuracy: 1.0`, one failed segment
  regenerated, three takes per segment, nine package artifacts, nine published
  files, and deterministic-local cost `0`.
- The immediate `--resume` run replayed nine render takes and retained the same
  package manifest checksum and publication identity; `status.json` retains the
  `published` stage in its history before `completed`.
- `make build`, `make docs`, `uv lock --check`, `uv pip check`, compileall,
  `git diff --check`, and the changed-scope credential audit passed.

Live provider rendering/transcription, hosted Temporal, payment reconciliation,
and non-filesystem publication remain intentionally deferred. Successful local
commands are not evidence of those live integrations.
