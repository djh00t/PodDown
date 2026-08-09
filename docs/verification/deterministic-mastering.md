# Deterministic mastering and media inspection verification

## Scope

This M2 slice adds a provider-neutral mastering boundary for accepted mono,
16-bit PCM WAV segments. It orders segments by stable position, inserts the
versioned profile's silence or crossfade policy, runs deterministic local
ffmpeg encoding, inspects the resulting MP3 with an injectable ffprobe
boundary, verifies the WAV and MP3 constraints, and records immutable command,
media, and checksum provenance.

The default path is local deterministic tooling. No hosted provider, paid
render, or live transcription call is part of this evidence.

## Acceptance evidence

BDD scenarios in
[`mastering.feature`](../../tests/features/mastering.feature) and bindings in
[`test_mastering.py`](../../tests/bdd/test_mastering.py) prove:

- out-of-order segments are dispatched in canonical position order;
- malformed audio fails before the runner is called;
- runner failures map to `MasteringError`;
- missing MP3 inspection evidence fails closed; and
- equivalent input bytes produce equal output checksums.

Unit coverage additionally proves pre-dispatch rejection of truncated, wrong
rate, wrong-channel, wrong-sample-width, clipped, too-short, and too-long
inputs; malformed provenance commands; profile-bound peak/clipping gates;
ambiguous ffprobe streams; and injected MP3-inspection failure.

## Verification record

Focused mastering gate:

```text
PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
.venv/bin/pytest -p pytest_bdd.plugin -q \
tests/bdd/test_mastering.py tests/unit/audio/test_mastering.py
35 passed
```

Focused quality checks:

```text
.venv/bin/ruff check src/poddown/audio/mastering.py \
src/poddown/audio/__init__.py tests/bdd/test_mastering.py \
tests/unit/audio/test_mastering.py
All checks passed

.venv/bin/mypy --strict
Success: no issues found in 34 source files
```

Repository changed-scope gate:

```text
PYDANTIC_DISABLE_PLUGINS=1 make check
489 passed, 1 live-provider test deselected in 14.87s
Total branch coverage: 87.27%; mastering module coverage: 81%
Ruff format/check passed; strict mypy passed
```

Additional delivery checks:

- `make build` produced `dist/poddown_studio-0.0.0.tar.gz` and
  `dist/poddown_studio-0.0.0-py3-none-any.whl`.
- `make docs` completed the API documentation build.
- `uv lock --check` resolved 34 packages without lock drift.
- `uv pip check` reported all 33 installed packages compatible.
- `10` tracked JSON/YAML files parsed successfully.
- `compileall -q src tests`, `git diff --check`, and the credential-pattern
  audit passed with no findings.

The local ffmpeg smoke path was run twice against equivalent input bytes. The
resolved executable was `/opt/homebrew/bin/ffmpeg`, version 8.1.2. WAV and MP3
bytes were identical across both runs, with checksums:

```text
WAV 191102e3cce686d8b6d8399409593301906e831e073c5512f9aaf3ff5d5aa2f2
MP3 4401e33d0907c9efc75af124aac355d99dff31ffcd96c23a4bb4b0cf31b49903
```

The complete changed-scope repository gate, build, documentation build,
schema validation, dependency audit, and clean-diff audit passed in this
worktree before the stacked-PR handoff.

## Provenance and failure boundaries

Every successful result records the profile version, resolved ffmpeg
executable and version, primary command, complete command list, filters,
ordered input checksums, WAV diagnostics, MP3 ffprobe metadata, and output
checksums. Metadata maps are copied into read-only mappings before they leave
the mastering service.

ffmpeg and ffprobe stderr is never returned in the public error message.
Missing executables, non-zero exits, timeouts, malformed metadata, ambiguous
streams, and output gate failures are terminal mastering errors.

## Explicit deferrals

This slice does not yet claim final-master transcription, critical-token QA of
the encoded episode, transcript/VTT/chapters/show notes, immutable nine-file
package assembly, publication, or live-provider evidence. Those remain the
next dependency-safe M2/M3 slices and are not represented as complete here.
