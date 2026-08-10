# Reference Episode Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one repeatable deterministic-local reference episode that exercises PodDown from Markdown intake through source-bound preparation, multi-take rendering, fidelity QA, mastering, immutable packaging, metering, resumable status, MCP preview, and filesystem publication.

**Architecture:** Keep the demo as an application-layer composition in `poddown.demo`; existing content, audio, QA, package, persistence, MCP, and publishing contracts remain the authorities. The runner reads versioned fixtures, persists all intermediate evidence beneath one caller-selected output directory, uses a local renderer and local transcription fixture, deliberately fails one render once, regenerates that segment, and emits a JSON summary that distinguishes deterministic-local mode from live-provider mode.

**Tech Stack:** Python 3.12+, stdlib `asyncio`/`wave`/`json`/`pathlib`, existing PodDown typed contracts, pytest-bdd, pytest, uv, Make, and the existing local `ffmpeg` mastering adapter.

## Global Constraints

- No live provider, credential, payment, or external Temporal account is used by the reference demo.
- Synthetic voice assets require explicit local consent evidence before every render dispatch.
- Source Markdown, frontmatter, script, lexicon/token evidence, render candidates, transcription, QA, package, publication, and usage records are written under the selected output directory.
- The final package must contain exactly the nine required package artifacts and critical-token accuracy must equal `1.0`.
- A failed segment must be observable in the result evidence and must be regenerated before package commit.
- Re-running with `--resume` must replay persisted render/package/publication evidence instead of silently claiming a new live dispatch.
- `make check-full` and `make quality-gates` remain CI-only; local verification uses focused tests and changed-scope `make check`.

---

## File Map

- Create `integrations/reference-demo/v1/source.md`: difficult two-speaker Markdown source with frontmatter, a 12-minute target, date, names, acronyms, units, percentages, repeated negation, and disagreement.
- Create `integrations/reference-demo/v1/profile.yaml`: the validated dialogue profile and two opaque synthetic voice asset IDs.
- Create `integrations/reference-demo/v1/adaptation.json`: source-bound turns, claims, anchors, and critical-token expectations.
- Create `integrations/reference-demo/v1/voices.yaml`: synthetic/demo-only voice metadata and consent IDs.
- Create `integrations/reference-demo/v1/disclosure.yaml`: spoken/show-note disclosure policy for the local demo.
- Create `src/poddown/demo.py`: fixture loader, deterministic local adapters, resumable reference-demo orchestration, result serialization, and `poddown-demo` CLI entrypoint.
- Create `tests/features/reference_demo.feature`: executable acceptance scenarios for the complete local path and resume path.
- Create `tests/bdd/test_reference_demo.py`: pytest-bdd bindings plus deterministic test doubles for mastering and transcription.
- Create `tests/unit/test_demo.py`: focused tests for fixture validation, failure/regeneration accounting, JSON result stability, and unsafe live-mode/consent rejection.
- Modify `pyproject.toml`: register `poddown-demo = "poddown.demo:main"`.
- Modify `Makefile`: add `.PHONY demo` and `make demo` with `DEMO_OUTPUT` override.
- Modify `README.md`: document the copy-paste reference-demo and MCP preview commands.
- Modify `docs/planning-traceability.md`: add the M8 reference-demo requirement-to-evidence row.
- Create `docs/verification/reference-demo.md`: deterministic/local versus live-mode boundary, output contract, verification commands, and intentional live-provider deferrals.

### Task 1: Add the versioned reference fixtures and red BDD contract

**Files:**
- Create: `integrations/reference-demo/v1/source.md`
- Create: `integrations/reference-demo/v1/profile.yaml`
- Create: `integrations/reference-demo/v1/adaptation.json`
- Create: `integrations/reference-demo/v1/voices.yaml`
- Create: `integrations/reference-demo/v1/disclosure.yaml`
- Create: `tests/features/reference_demo.feature`
- Create: `tests/bdd/test_reference_demo.py`

**Interfaces:**
- The fixture loader will consume UTF-8 `source.md`, YAML `profile.yaml`, JSON `adaptation.json`, YAML `voices.yaml`, and YAML `disclosure.yaml`.
- The BDD binding will call `run_reference_demo(output_dir: Path, *, resume: bool = False, mastering_runner: FfmpegRunner | None = None) -> DemoResult`.
- `DemoResult.to_dict()` will expose `mode`, `source_sha256`, `profile_id`, `segment_ids`, `take_count`, `selected_candidate_ids`, `failed_segment_ids`, `regenerated_segment_ids`, `critical_token_accuracy`, `package_artifacts`, `publication_path`, `usage`, `cost`, `replayed_takes`, and `mcp_preview`.

- [x] **Step 1: Write the failing feature and binding assertions.**

  The feature must contain these scenarios:

  ```gherkin
  Feature: Reference episode demo

    Scenario: Complete the difficult technical dialogue in deterministic local mode
      Given an empty reference demo output directory
      When the reference episode demo is run
      Then the result records a validated source profile and two speakers
      And three takes are rendered for every segment with stable voice bindings
      And one failed segment is regenerated before QA
      And final critical-token accuracy is 1.0
      And the package contains the nine required artifacts
      And publication is a filesystem demo publication
      And the MCP preview reports no side effect

    Scenario: Resume the completed reference episode from persisted evidence
      Given a completed reference episode demo
      When the reference episode demo is resumed
      Then the result reports replayed render takes
      And the package and publication identities are unchanged
  ```

  The step definitions must assert the exact result fields above, `mode == "deterministic-local-demo"`, `target_minutes == 12`, two distinct speakers, `take_count == 3`, one failed and regenerated segment, `critical_token_accuracy == 1.0`, the set of nine names in `REQUIRED_PACKAGE_ARTIFACTS`, `usage["render_requests"] > 0`, `cost == "0"`, and `mcp_preview["result"]["side_effect"] == "none"`.

- [x] **Step 2: Run the BDD scenario to verify the expected red failure.**

  Run: `uv run pytest tests/bdd/test_reference_demo.py -q`

  Expected: FAIL because `poddown.demo` and `run_reference_demo` do not exist yet.

- [x] **Step 3: Validate fixture content before implementation.**

  Run: `python - <<'PY'` with a short script that loads `source.md`, parses its `poddown` frontmatter, and asserts the source includes `2026-07-31`, `LiDAR`, `C1`, `99.7%`, `1.2 km`, both occurrences of `not`, and `I disagree`; assert the profile has two speakers and `target_minutes == 12`.

- [x] **Step 4: Commit the red contract and fixtures.**

  ```bash
  git add integrations/reference-demo tests/features/reference_demo.feature tests/bdd/test_reference_demo.py
  git commit -m "test(demo): define reference episode acceptance path"
  ```

### Task 2: Implement the deterministic reference-demo composition

**Files:**
- Create: `src/poddown/demo.py`
- Create: `tests/unit/test_demo.py`

**Interfaces:**
- `@dataclass(frozen=True, slots=True) class DemoResult` stores JSON-safe result evidence and exposes `to_dict() -> dict[str, object]`.
- `def run_reference_demo(output_dir: Path, *, resume: bool = False, mastering_runner: FfmpegRunner | None = None) -> DemoResult` is the public orchestration entrypoint.
- `def main(argv: Sequence[str] | None = None) -> int` accepts `--output PATH` and `--resume`; it prints one sorted JSON result and returns `0` only after `DemoResult` reports completed state.
- `_load_reference_fixture(fixture_root: Path) -> _ReferenceFixture` must validate file encodings, frontmatter, profile speakers, synthetic/demo-only voice metadata, disclosure, and source-bound adaptation values before dispatch.
- `_build_content_request(fixture: _ReferenceFixture) -> ContentPreparationRequest` must call the typed preparation service or its existing compatibility facade and return a source-bound `ContentPreparationResult`.
- `_render_segments(...) -> tuple[_RenderedSegment, ...]` must call `DurableRenderService.render_takes(..., take_count=3)` with `AudioVoiceConsent` for every segment and use `select_candidate` over `CandidateQuality` values.
- `_write_status(output_dir: Path, stage: str, evidence: Mapping[str, object]) -> None` writes `status.json` atomically after each stage.

- [x] **Step 1: Add unit tests for fixture and unsafe boundary behavior.**

  Include tests that assert: missing fixture files fail closed; a voice with `synthetic: false` fails before rendering; a voice without `consent_status: approved` fails before rendering; `run_reference_demo` never accepts a `live` mode argument; `DemoResult.to_dict()` is JSON serializable; and the output directory is rejected when it is a file.

- [x] **Step 2: Run the focused unit tests to observe the expected red state.**

  Run: `uv run pytest tests/unit/test_demo.py -q`

  Expected: FAIL with the missing module/entrypoint errors from Task 1.

- [x] **Step 3: Implement fixture loading and source-bound preparation.**

  Load the fixture files, call `snapshot_source`, construct the existing typed/compatibility content request, call `prepare_content`, and reject an unaccepted result. Preserve `source_sha256`, `profile_id`, `target_minutes`, canonical script hash, token evidence, segment IDs, and the full preparation manifest in the result evidence. Do not import test helpers from `tests/`.

- [x] **Step 4: Implement deterministic rendering, multi-take selection, failure, and regeneration.**

  Add a private renderer wrapper that returns one empty `RenderedAudio` for the first call to the first segment, causing `DurableRenderService` to raise `RenderRejectedError`. Catch that expected failure, record its segment ID and error code in `status.json`, rerun the same request with `DeterministicLocalRenderer`, render exactly three takes, diagnose each WAV, evaluate segment critical tokens, build `CandidateQuality`, call `select_candidate`, and retain the selected artifact reference. Every request must use provider `local`, model `local-deterministic-v1`, an opaque approved voice asset, and local consent evidence.

- [x] **Step 5: Implement deterministic transcription, mastering, final QA, package, metering, status, and publication.**

  Build one deterministic transcript from each prepared script turn by replacing source forms with `CriticalToken.expected_spoken_form`, create word timings, and return a `TranscriptResult` whose checksum is computed from the exact master bytes, provider is `local-deterministic-demo`, mode is `deterministic-local-demo`, and cost is zero. Use `MasteringService` with `SubprocessFfmpegRunner` by default (and the injected runner in tests), `FinalMasterQaService`, `generate_package_artifacts`, `EpisodePackageService.commit`, `SQLiteUsageLedger`, and `PublishingService` with `FilesystemPublicationAdapter`. Use UUIDv7-compatible fixed demo IDs, an explicit local publication authorization, and an immutable idempotency key. Persist `result.json`, `status.json`, `usage.json`, `publication.json`, and the package manifest beneath `output_dir`; update status through `ingested`, `prepared`, `rendering`, `qa`, `mastering`, `packaged`, `published`, and `completed`.

- [x] **Step 6: Make resume replay-safe.**

  On `resume=True`, reuse the same output directory, request identities, SQLite ledger, artifact store, package root, render records, and publication idempotency key. Count `RenderOutcome.replayed` values, return `replayed_takes > 0`, and assert the package manifest checksum and publication external ID match the first run. Never convert a previous failure into a successful live-provider claim.

- [x] **Step 7: Run the focused BDD and unit tests.**

  Run: `uv run pytest tests/bdd/test_reference_demo.py tests/unit/test_demo.py -q`

  Expected: PASS with both scenarios and all boundary tests passing; no live-provider marker is enabled.

- [x] **Step 8: Commit the implementation.**

  ```bash
  git add src/poddown/demo.py tests/unit/test_demo.py
  git commit -m "feat(demo): run deterministic reference episode end to end"
  ```

### Task 3: Wire CLI, Make, traceability, and operating documentation

**Files:**
- Modify: `pyproject.toml`
- Modify: `Makefile`
- Modify: `README.md`
- Modify: `docs/planning-traceability.md`
- Create: `docs/verification/reference-demo.md`

**Interfaces:**
- `poddown-demo --output PATH [--resume]` invokes `poddown.demo:main`.
- `make demo DEMO_OUTPUT=/tmp/poddown-reference-demo` invokes `uv run poddown-demo --output "$DEMO_OUTPUT"`.
- Documentation must show the CLI path, the MCP `poddown_preview` path, expected output files, deterministic-local/live distinction, resume command, and exact verification commands.

- [x] **Step 1: Add CLI and Make contract tests.**

  Extend the existing Make/CLI contract tests with assertions that `pyproject.toml` declares `poddown-demo`, `Makefile` declares `demo`, and the documented command contains `--output` and `--resume`.

- [x] **Step 2: Run the contract tests before wiring.**

  Run: `uv run pytest tests/unit/test_makefile.py tests/bdd/test_cli.py -q`

  Expected: FAIL on the missing `poddown-demo` script and `demo` target.

- [x] **Step 3: Wire the command and Make target.**

  Add the script and target without changing existing CLI/API/MCP behavior. Use `/tmp/poddown-reference-demo` only as the Make default and allow `DEMO_OUTPUT` to override it.

- [x] **Step 4: Write documentation and traceability evidence.**

  Document that local rendering/transcription are deterministic fixtures, local publication writes a filesystem target, no provider credentials or provider spend is evidence, and live provider/hosted Temporal/publication adapters remain explicit deferrals. Add the M8 requirement mapping to `docs/planning-traceability.md` with implementation files, BDD/unit tests, verification file, and demo command.

- [x] **Step 5: Run CLI and documentation checks.**

  Run: `uv run poddown-demo --output /tmp/poddown-reference-demo`, then `uv run poddown-demo --output /tmp/poddown-reference-demo --resume`, then `make docs`.

  Expected: both commands exit `0`; the second reports replayed takes; `result.json`, `status.json`, `usage.json`, `publication.json`, the immutable package manifest, and all nine package files exist; documentation builds successfully.

- [x] **Step 6: Commit the integration and docs.**

  ```bash
  git add pyproject.toml Makefile README.md docs/planning-traceability.md docs/verification/reference-demo.md
  git commit -m "docs(demo): document repeatable reference episode"
  ```

### Task 4: Verify, review, and hand off the M8 branch

**Files:**
- Verify all M8 files from Tasks 1–3.
- Update: `docs/superpowers/plans/2026-08-10-reference-demo.md` checkboxes and `docs/verification/reference-demo.md` with fresh outputs.

- [x] **Step 1: Run the focused reference-demo verification.**

  ```bash
  uv run pytest tests/features/reference_demo.feature tests/bdd/test_reference_demo.py tests/unit/test_demo.py -q
  uv run pytest tests/bdd/test_signal_supply.py tests/bdd/test_package_generation.py tests/bdd/test_final_master_qa.py tests/bdd/test_publishing.py -q
  make check
  make build
  make docs
  uv lock --check
  uv run pip check
  git diff --check
  git diff --name-only | xargs rg -n '(OPENAI_API_KEY|ELEVENLABS_API_KEY|secret_[A-Za-z0-9]+|-----BEGIN)' || true
  ```

  Expected: every required command passes; the credential audit prints no matches; no live-provider test is enabled.

- [x] **Step 2: Request an independent code review.**

  Ask a Terra reviewer to inspect the M8 diff against the reference-demo acceptance criteria, with special attention to source fidelity, consent, idempotency, replay status, package artifact completeness, and claims that could confuse deterministic fixtures with live provider evidence.

- [ ] **Step 3: Fix every valid Critical, Important, or acceptance-blocking finding.**

  Add a regression test before each behavior change, rerun the focused suite and `make check`, and record the review response in the PR conversation.

  Review remediation tasks:

  - [ ] Bundle the versioned reference fixtures into the wheel and resolve them
    through package resources so `poddown-demo` works after installation.
  - [ ] Publish the disclosure text in the show-notes artifact and set receipt
    policy flags only for disclosure channels actually represented by the demo.
  - [ ] Bind resume-time `result.json` fields to immutable package provenance
    and persisted render evidence; reject tampered result evidence.

- [ ] **Step 4: Re-run the complete review and verification loop.**

  Reply to each review thread with the changed files and focused/full-gate
  evidence, resolve each thread only after its fix is pushed, and confirm the
  exact PR head is green and conflict-free.

- [ ] **Step 5: Push a normal ready PR stacked on `codex/m7-production-readiness`.**

  Read `/Users/djh/.codex/AGENTS-DELIVERY.md`, use a Conventional Commit/PR title, include the parent branch, acceptance behavior, verification evidence, demo commands, deterministic/live limitations, and no draft flag. Monitor CI until all required checks are green; do not merge or approve.

## Spec Coverage Self-Review

- Markdown/frontmatter intake: Task 1 fixture plus Task 2 `snapshot_source` and source hash evidence.
- Profiles and two-speaker dialogue: Task 1 profile plus Task 2 profile validation and voice-bound render requests.
- Source-bound adaptation, pronunciation, critical tokens, segmentation: Task 1 adaptation/expectations plus Task 2 typed content preparation and manifest.
- Multi-take generation, selection, failure regeneration: Task 2 render wrapper, three takes, `CandidateQuality`, `select_candidate`, and replay evidence.
- Transcription/fidelity/audio QA/mastering: Task 2 deterministic transcript, `FinalMasterQaService`, and `MasteringService` provenance.
- Episode package/provenance/chapters/show notes: Task 2 `generate_package_artifacts` and `EpisodePackageService`; Task 3 documentation.
- Async/status path and usage/cost: Task 2 status checkpoints, resume replay, and `SQLiteUsageLedger`.
- MCP/API path: Task 2 side-effect-free `poddown_preview` and Task 3 copy-paste MCP command; existing API remains unchanged.
- Safe publication: Task 2 filesystem adapter, explicit authorization, scoped target, and immutable idempotency key.
- Production/live limitations: Task 3 verification documentation explicitly distinguishes local deterministic evidence from live-provider readiness.
