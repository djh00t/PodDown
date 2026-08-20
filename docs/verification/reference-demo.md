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

Current reconciliation evidence on 2026-08-15:

- An attempt using the exact clean `main` fixture and renderer artifacts
  completed host-local speech rendering but reproduced the fixed 120-second
  FFmpeg mastering timeout. Resuming that persisted output with the preserved
  reconciliation timeout correction completed mastering, packaging, and
  filesystem publication; no provider credential or network call was used.
- The published MP3 is **650.031 seconds**, mono, 44.1 kHz, with **9** package
  artifacts and **9** published files.
- `result.json` records cost `0`, script-derived critical-token accuracy `1.0`,
  no failed segments, and **306** replayed render takes on both the recovery
  and immediate resume runs. The package manifest digest is
  `9a4491559be76cb1144c1364c6d1c950a377d165bb73fa74e25c25cbbc190f0a`.
- The duration-scaled FFmpeg timeout is present only in the uncommitted
  reconciliation worktree; current `main` still needs that correction delivered.
  This is host-local speech/package evidence only and does not establish
  provider ASR fidelity, hosted runtime recovery, authenticated UAT, or release
  readiness.

Fresh host-local reconciliation run on 2026-08-15:

- `.venv/bin/poddown-demo --audio-mode local-speech --output
  /tmp/poddown-host-local-closure-20260815` completed with
  `mode: local-system-tts-demo`, zero provider cost, and script-derived
  critical-token accuracy `1.0`.
- FFprobe measured a **650.031-second**, mono, 44.1 kHz MP3. Its SHA-256 is
  `b21260de0fbe9e0d63ce8ad7b35d21dd0ab7983efdc3633459d907ee3a20dd15`.
- The package contained the expected **9** artifacts. Its manifest digest is
  `d6c1f8cd2aafa6a525ec503111df7b332ce55e29cb5ef64db03d2f0ac0f2ab21`.
- QA passed with zero clipping. The workflow recorded **307** render requests,
  one failed segment, and regeneration of that same segment; no provider
  credential or network call was used.
- This run exercised the duration-scaled FFmpeg timeout in the uncommitted
  reconciliation worktree. It is stronger fresh host-local artifact evidence,
  but it is not clean-`main` delivery evidence and does not establish provider
  ASR fidelity, hosted runtime recovery, authenticated UAT, or release
  readiness.

Live ElevenLabs rendering is separate from both local modes. It requires an
explicit `ELEVENLABS_API_KEY`, approved provider voice mapping, rights/consent
evidence, and spending authorization. Successful local commands are not
evidence of that live integration, hosted Temporal, payment reconciliation, or
non-filesystem publication.
