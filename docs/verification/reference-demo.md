# Reference demo verification

The default M8 reference demo is `local-system-tts-demo`. It uses versioned
source, profile, adaptation, voice-consent, and disclosure fixtures; a host-local
speech renderer, deterministic transcription fixture, local ffmpeg adapter,
SQLite ledger, filesystem package storage, and filesystem publication adapter.
It does not use a provider credential, provider spend, hosted Temporal, payment,
or external publication adapter. `deterministic-local-demo` remains an explicit
structural mode for stable offline tests, not listenable host-speech evidence.

The default local mode requires the `ffmpeg` package, which provides both
`ffmpeg` and `ffprobe`, plus macOS `say` or Linux `espeak-ng`/`espeak`. On macOS
use `brew install ffmpeg`; on Debian/Ubuntu use `sudo apt-get update && sudo
apt-get install --no-install-recommends -y ffmpeg espeak-ng`. Check the selected
host before running it:

```bash
command -v ffmpeg ffprobe
command -v say || command -v espeak-ng || command -v espeak
```

Missing tooling fails closed before publication. Host-local MP3 bytes can vary
with the OS, voice, and speech-engine version, so inspect the artifact rather
than comparing bytes across hosts.

The versioned fixtures are bundled into the wheel under
`poddown/reference-demo/v1`; the installed-wheel CLI path is part of this
verification. The demo publishes the disclosure text in `show-notes.md` and
records `spoken: false`, `show_notes: true`, and `platform: false` because no
spoken intro or external platform publication is performed.

Run it with:

```bash
uv run poddown-demo --output /tmp/poddown-reference-demo
uv run poddown-demo --output /tmp/poddown-reference-demo --resume
uv run poddown-demo --audio-mode deterministic --output /tmp/poddown-reference-demo-structural
uv run pytest tests/bdd/test_reference_demo.py tests/unit/test_demo.py -q
```

The first command writes source/preparation/render/QA/mastering/package/usage
and publication evidence under its output directory. The package has exactly
nine artifacts. The second command replays persisted render evidence and keeps
the immutable package manifest and filesystem publication identity unchanged.
The MP3 is under the output directory's published package tree; inspect it with:

```bash
afinfo "$(find /tmp/poddown-reference-demo -type f -name episode.mp3 -print -quit)"
ffprobe -v error -show_entries format=duration:stream=codec_name,sample_rate,channels -of json "$(find /tmp/poddown-reference-demo -type f -name episode.mp3 -print -quit)"
```

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

Live ElevenLabs rendering is separate from both local modes. It requires an
explicit `ELEVENLABS_API_KEY`, approved provider voice mapping, rights/consent
evidence, and spending authorization. Successful local commands are not
evidence of that live integration, hosted Temporal, payment reconciliation, or
non-filesystem publication.
