# Listenable Local Reference Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the default reference-demo command produce a genuinely listenable, two-speaker 10–15 minute local episode while retaining the deterministic renderer for offline tests and keeping ElevenLabs explicitly credential-gated.

**Architecture:** Add a host-local speech renderer behind the existing `AudioRenderer` contract. It discovers macOS `say` or Linux `espeak-ng`/`espeak`, normalizes output with the existing `ffmpeg` boundary, validates canonical WAV diagnostics, and records host-tool provenance. Inject the renderer into the reference-demo composition; direct tests continue using the deterministic renderer, and the CLI selects local speech by default with an explicit deterministic override.

**Tech Stack:** Python 3.12, stdlib `subprocess`/`tempfile`/`wave`, existing `ffmpeg`/`ffprobe`, `pytest-bdd`, pytest, uv, Make, and the existing PodDown audio/content/package contracts.

## Global Constraints

- No live provider, API key, account, payment, or external network call is used by the local reference demo.
- ElevenLabs remains a separate explicit live mode requiring `ELEVENLABS_API_KEY`, approved provider voice mapping, and rights evidence.
- Host-local output is labeled `local-system-tts-demo` and records engine, executable, version, voice, and normalization provenance.
- The existing deterministic renderer remains available for offline CI and direct test composition.
- The reference fixture contains a source-bound 10–15 minute two-speaker technical dialogue with required names, acronyms, dates, percentages, units, repeated critical tokens, negation, uncertainty, and disagreement.
- Every behavior change has executable BDD coverage plus focused unit and integration tests.
- Missing host speech tooling fails closed before publication; no silent noise or live-provider fallback is allowed.
- Local verification uses changed-scope `make check`; `make check-full` and `make quality-gates` remain CI-only.

---

### Task 1: Add the host-local speech renderer contract

**Files:**
- Create: `src/poddown/audio/speech.py`
- Modify: `src/poddown/audio/__init__.py`
- Create: `tests/features/local_speech.feature`
- Create: `tests/bdd/test_local_speech.py`
- Create: `tests/unit/audio/test_speech.py`

**Interfaces:**
- `LocalSpeechRenderer` implements the existing `AudioRenderer` protocol and exposes `provider = "host-local"`, `model = "host-local-tts-v1"`, `mode = "local-system-tts-demo"`, and `provenance() -> Mapping[str, object]`.
- `LocalSpeechRenderer.__init__(engine: str = "auto", voices: Mapping[str, str] | None = None, process_runner: SpeechProcessRunner | None = None, ffmpeg_executable: str = "ffmpeg")` selects a host engine without reading credentials.
- `SpeechProcessRunner.run(command: tuple[str, ...], *, cwd: Path, timeout_seconds: float) -> None` is the injectable subprocess boundary used by unit tests.
- `LocalSpeechRenderer.render(request: RenderRequest) -> RenderedAudio` returns mono 16-bit 44.1 kHz WAV bytes with zero cost and request-matching provider/model metadata.

- [ ] **Step 1: Write failing BDD scenarios and unit tests.**

  Add scenarios for: an available local engine producing canonical speech WAV, a missing executable failing closed, and the same text/voice being cached across three take requests. Unit tests must inject a fake process runner that writes valid low-amplitude PCM WAV bytes, assert explicit argv for `say` and `espeak`, assert `ffmpeg` normalization, reject malformed/truncated output, reject duration below the local speech minimum, and verify no environment credential is read.

- [ ] **Step 2: Run the new tests to observe the expected RED state.**

  Run:

  ```bash
  uv run pytest tests/bdd/test_local_speech.py tests/unit/audio/test_speech.py -q
  ```

  Expected: collection or import failure because `poddown.audio.speech.LocalSpeechRenderer` and its test boundary do not exist.

- [ ] **Step 3: Implement engine discovery and safe subprocess execution.**

  Resolve `say` on macOS first; otherwise resolve `espeak-ng`, then `espeak`. Resolve `ffmpeg` with `shutil.which`. Build argument tuples only, use a temporary directory per render, capture process output without logging speech text, and bound each process to 120 seconds. Raise a stable `LocalSpeechError` naming the missing executable or failed stage without exposing command output or credentials.

- [ ] **Step 4: Implement canonical normalization, caching, and metadata.**

  Render to an engine-native temporary file, run `ffmpeg` with `-ac 1 -ar 44100 -c:a pcm_s16le`, read the normalized WAV, call `diagnose_wav` with a local speech minimum of 0.1 seconds and zero clipping ratio, and return `RenderedAudio`. Cache normalized bytes by `(speaker_id, voice_asset_id, expected_spoken_text, sample_rate_hz)`, but create a request-specific `request_id` and usage record for every candidate. Record engine, executable, voice binding, and ffmpeg details through `provenance()`.

- [ ] **Step 5: Run the focused tests to verify GREEN.**

  Run:

  ```bash
  uv run pytest tests/bdd/test_local_speech.py tests/unit/audio/test_speech.py -q
  ```

  Expected: all new BDD and unit tests pass without invoking a real speech engine or hosted provider.

- [ ] **Step 6: Commit the renderer slice.**

  ```bash
  git add src/poddown/audio/speech.py src/poddown/audio/__init__.py tests/features/local_speech.feature tests/bdd/test_local_speech.py tests/unit/audio/test_speech.py
  git commit -m "feat(audio): add host-local speech renderer"
  ```

### Task 2: Inject renderer mode into the reference-demo workflow

**Files:**
- Modify: `src/poddown/demo.py`
- Modify: `tests/features/reference_demo.feature`
- Modify: `tests/bdd/test_reference_demo.py`
- Modify: `tests/unit/test_demo.py`
- Modify: `tests/unit/audio/test_render.py` only if a new provider identity regression requires it

**Interfaces:**
- Extend `run_reference_demo(output_dir: Path, *, resume: bool = False, mastering_runner: FfmpegRunner | None = None, renderer: AudioRenderer | None = None) -> DemoResult`; `renderer=None` preserves deterministic mode for direct tests.
- Thread the renderer through `_render_segments`; derive request provider/model and consent provider from the renderer’s declared identity instead of hardcoded deterministic values.
- `_FailOnceRenderer(delegate: AudioRenderer, failed_segment_id: str)` delegates after the deliberate first failure and exposes the delegate’s capabilities/provider/model/mode/provenance.
- `main(argv)` accepts `--audio-mode {local-speech,deterministic}` and defaults to `local-speech`; it passes `LocalSpeechRenderer` for local speech and `DeterministicLocalRenderer` for deterministic mode.

- [ ] **Step 1: Add failing BDD and unit assertions.**

  Add assertions that an injected host-local renderer produces `mode == "local-system-tts-demo"`, uses `host-local` request identity, records local engine provenance, keeps three-take/regeneration accounting, and rejects resume when persisted result mode or renderer identity does not match. Add CLI contract assertions for the new default and deterministic override. Keep existing deterministic result assertions unchanged for direct test calls.

- [ ] **Step 2: Run the focused demo tests to observe the expected RED state.**

  Run:

  ```bash
  uv run pytest tests/bdd/test_reference_demo.py tests/unit/test_demo.py -q
  ```

  Expected: failures for the missing renderer injection, dynamic mode/provenance, and CLI option.

- [ ] **Step 3: Implement renderer injection and identity-safe request construction.**

  Default `renderer` to `DeterministicLocalRenderer` inside `run_reference_demo`; make `_render_segments` use `renderer.provider`, `renderer.model`, and `renderer.mode`. Build `VoiceConsent` with the actual renderer provider. Preserve request identity across resume by validating the persisted mode/provider/model before accepting replay evidence. Keep the package workflow and result accounting unchanged apart from dynamic renderer provenance.

- [ ] **Step 4: Add final local-speech duration and quality gates.**

  Compute the deterministic transcript before mastering. For local speech mode, require a master duration of at least `max(1.0, len(transcript_words) / 5.0)` seconds and use a profile peak limit below full scale; for deterministic mode retain the existing structural minimum. Reject the package before publication when the local speech result is too short, clipped, malformed, or missing provenance.

- [ ] **Step 5: Wire the CLI and run focused tests.**

  Run:

  ```bash
  uv run pytest tests/bdd/test_reference_demo.py tests/unit/test_demo.py tests/unit/audio/test_render.py -q
  ```

  Expected: deterministic direct tests and injected host-local BDD tests pass; no test invokes a real host speech binary.

- [ ] **Step 6: Commit the workflow slice.**

  ```bash
  git add src/poddown/demo.py tests/features/reference_demo.feature tests/bdd/test_reference_demo.py tests/unit/test_demo.py
  git commit -m "feat(demo): select listenable local speech mode"
  ```

### Task 3: Expand the source-bound reference episode

**Files:**
- Modify: `integrations/reference-demo/v1/source.md`
- Modify: `integrations/reference-demo/v1/adaptation.json`
- Modify: `integrations/reference-demo/v1/voices.yaml`
- Modify: `tests/unit/test_demo.py` for fixture-length and local voice binding checks

**Interfaces:**
- Preserve the existing fixture file names and `profile_id`, two speaker IDs, synthetic/demo-only consent, and profile target of 12 minutes.
- Add local voice metadata per approved demo asset without adding provider voice IDs or credentials.
- Keep every adaptation turn source-bound to a Markdown claim anchor and retain expected spoken forms for all critical tokens.

- [ ] **Step 1: Add failing fixture assertions.**

  Assert the source contains 1,600–2,000 spoken words, at least 12 turns, both speaker IDs, `LiDAR`, `C1`, the date, percentage, unit, repeated negation, uncertainty, and disagreement. Assert each adaptation turn is anchored to a source block and each profile voice asset has local voice metadata.

- [ ] **Step 2: Run the fixture tests to observe the expected RED state.**

  Run:

  ```bash
  uv run pytest tests/unit/test_demo.py tests/unit/test_signal_supply_fixtures.py -q
  ```

  Expected: the existing three-turn fixture fails the new word-count, turn-count, and local-voice assertions.

- [ ] **Step 3: Replace the short fixture with a source-bound dialogue.**

  Write a 10–15 minute technical conversation with the host and analyst covering measurement method, calibration, uncertainty, rain and reflective surfaces, dates, percentages, distances, units, acronym pronunciation, publication limits, and a counter-thesis. Add repeated exact critical-token occurrences and explicit negation without introducing unsupported facts. Add matching adaptation claims and turn rows with anchors and adapted values.

- [ ] **Step 4: Verify fixture invariants and content preparation.**

  Run:

  ```bash
  uv run pytest tests/unit/test_demo.py tests/bdd/test_reference_demo.py tests/integration/test_content_pipeline.py -q
  ```

  Expected: source-bound preparation succeeds, all required tokens have expected spoken forms, and segmentation preserves both speaker IDs and turn order.

- [ ] **Step 5: Commit the fixture slice.**

  ```bash
  git add integrations/reference-demo/v1/source.md integrations/reference-demo/v1/adaptation.json integrations/reference-demo/v1/voices.yaml tests/unit/test_demo.py
  git commit -m "feat(demo): provide full-length technical dialogue fixture"
  ```

### Task 4: Document local versus live rendering and operating commands

**Files:**
- Modify: `README.md`
- Modify: `Makefile`
- Modify: `docs/verification/reference-demo.md`
- Modify: `docs/provider-guidelines.md`
- Modify: `docs/architecture.md`
- Modify: `docs/planning-traceability.md`
- Modify: `tests/unit/test_makefile.py`
- Modify: `tests/bdd/test_cli.py`

**Interfaces:**
- `poddown-demo --output PATH` runs host-local speech mode by default.
- `poddown-demo --audio-mode deterministic --output PATH` runs the offline structural fixture mode.
- Documentation shows `ffmpeg`/`ffprobe` plus macOS `say` or Linux `espeak-ng`/`espeak`, clearly labels local-system versus live ElevenLabs evidence, and provides audio inspection commands.

- [ ] **Step 1: Add failing documentation/Make/CLI contract assertions.**

  Assert README and Make text describe both audio modes, required local binaries, the no-key default, and the explicit `ELEVENLABS_API_KEY` live boundary.

- [ ] **Step 2: Update command wiring and documentation.**

  Keep `make demo` explicit about its selected mode; document `command -v` checks, `afinfo`/`ffprobe` inspection, resume behavior, output location, missing-tool failure, and the fact that host-local bytes vary by OS/voice-engine version.

- [ ] **Step 3: Run docs and CLI checks.**

  Run:

  ```bash
  uv run pytest tests/unit/test_makefile.py tests/bdd/test_cli.py -q
  make docs
  ```

- [ ] **Step 4: Commit documentation and contracts.**

  ```bash
  git add README.md Makefile docs/verification/reference-demo.md docs/provider-guidelines.md docs/architecture.md docs/planning-traceability.md tests/unit/test_makefile.py tests/bdd/test_cli.py
  git commit -m "docs(demo): document local speech and live provider modes"
  ```

### Task 5: Verify the listenable demo and hand off the branch

**Files:**
- Verify all files from Tasks 1–4.
- Update: `docs/verification/reference-demo.md` with fresh artifact evidence.

- [ ] **Step 1: Run the actual macOS local-speech demo.**

  ```bash
  uv run poddown-demo --output /tmp/poddown-reference-demo-listenable
  uv run poddown-demo --output /tmp/poddown-reference-demo-listenable --resume
  find /tmp/poddown-reference-demo-listenable -type f -name episode.mp3 -print
  afinfo "$(find /tmp/poddown-reference-demo-listenable -type f -name episode.mp3 -print -quit)"
  ffprobe -v error -show_entries format=duration:stream=codec_name,sample_rate,channels -of json "$(find /tmp/poddown-reference-demo-listenable -type f -name episode.mp3 -print -quit)"
  ```

  Verify the output mode, two voice bindings, 100% critical-token accuracy, regeneration, zero cost, package artifacts, and a duration consistent with the expanded script.

- [ ] **Step 2: Run changed-scope quality verification.**

  ```bash
  make check
  make build
  make docs
  uv lock --check
  uv run pip check
  git diff --check
  git diff --name-only | xargs rg -n '(OPENAI_API_KEY|ELEVENLABS_API_KEY|secret_[A-Za-z0-9]+|-----BEGIN)' || true
  ```

  Do not run `make check-full` or `make quality-gates` locally.

- [ ] **Step 3: Request independent review.**

  Ask a Terra reviewer to inspect renderer command safety, rights/consent identity, local/live labeling, cache/request identity, resume rejection, fixture source fidelity, and audio duration/clipping gates against this plan.

- [ ] **Step 4: Resolve review findings with regression tests.**

  Verify each finding against the codebase, add a failing regression test before each valid behavior change, run focused checks and changed-scope `make check`, reply with evidence, and resolve only after the fix is present.

- [ ] **Step 5: Run final verification and prepare the branch for handoff.**

  Re-run the actual demo inspection and changed-scope checks on the final commit, confirm the worktree diff is limited to this plan, then use the finishing-development-branch workflow. Do not merge or approve the PR autonomously.

## Plan self-review

- The design requirement for a real local audio path is covered by Tasks 1, 2, and 5.
- Deterministic test isolation is covered by renderer injection and Task 2 regression assertions.
- The 10–15 minute source requirement is covered by Task 3 and its content-preparation verification.
- ElevenLabs credential/rights boundaries are preserved and documented in Tasks 1 and 4.
- Missing tooling, malformed audio, short duration, clipping, resume identity, package evidence, and source fidelity each have explicit test or verification steps.
- No task depends on a vague placeholder or an unowned file; the parent agent owns integration and final verification while fixture/documentation work can proceed in disjoint paths.
