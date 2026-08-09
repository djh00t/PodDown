# Task 1 implementation report (M1 Content Intelligence)

## Status
DONE

## Commit
- Awaiting commit until this report is written, then recorded below.

## Changed files
- `tests/features/content_intelligence.feature` (new)
- `tests/bdd/test_content_intelligence.py` (new)
- `tests/fixtures/content/robotics-mapping.md` (new)
- `tests/fixtures/content/technical-dialogue-profile.yaml` (new)
- `tests/fixtures/content/robotics-adaptation.json` (new)
- `/Users/djh/Documents/ChatGPT/PodDown/.worktrees/m1-content-intelligence/.superpowers/sdd/2026-08-09-content-intelligence/task-1-report.md` (new)

## Commands run
- `cat /Users/djh/.codex/AGENTS-RTK.md`
- `cat /Users/djh/.codex/AGENTS-CONTEXT-MODE.md`
- `cat /Users/djh/Documents/ChatGPT/PodDown/AGENTS.md`
- `cat /Users/djh/Documents/ChatGPT/PodDown/.worktrees/m1-content-intelligence/.superpowers/sdd/2026-08-09-content-intelligence/task-1-brief.md`
- `cat tests/bdd/test_intake.py`
- `cat tests/bdd/conftest.py`
- `cat tests/features/provider_rendering.feature`
- `cat tests/features/fidelity_qa.feature`
- `ls -1 tests/fixtures`
- `ls -R tests/fixtures`
- `ls -1 tests/features`
- `rtk uv run pytest tests/bdd/test_content_intelligence.py -v`

## Focused validation output
Result: 6 failed in 0.18s for `tests/bdd/test_content_intelligence.py`

Observed failure pattern for all six scenarios:
- `AssertionError: poddown.content.<module> is not implemented` with `ModuleNotFoundError: No module named 'poddown.content'`.

This confirms the RED run fails because the M1 public behavior ports are absent rather than a malformed feature/fixture setup.

## Commit SHA
- `cf8e28d`

## Residual risks
- Assertions in `Then` steps are currently conservative and assume future result object shapes.
- Scenario data currently uses `word * 5000` fixture text and may create extremely long in-memory strings, but this is fixture-only and contained.

## Task-1 Fix Round 1 report

### Changed files
- `tests/features/content_intelligence.feature`
- `tests/bdd/test_content_intelligence.py`
- `tests/fixtures/content/robotics-mapping.md`
- `tests/fixtures/content/technical-dialogue-profile.yaml`
- `tests/fixtures/content/robotics-adaptation.json`

### Commands run
- `cat .superpowers/sdd/2026-08-09-content-intelligence/task-1-review-round-1.md`
- `cat tests/features/content_intelligence.feature`
- `cat tests/bdd/test_content_intelligence.py`
- `cat tests/fixtures/content/robotics-mapping.md`
- `cat tests/fixtures/content/technical-dialogue-profile.yaml`
- `cat tests/fixtures/content/robotics-adaptation.json`
- `rtk uv run pytest tests/bdd/test_content_intelligence.py -v`

### Exact focused validation output
```
FAILED tests/bdd/test_content_intelligence.py::test_prepare_a_difficult_twospeaker_technical_source
FAILED tests/bdd/test_content_intelligence.py::test_reject_a_changed_number_before_rendering
FAILED tests/bdd/test_content_intelligence.py::test_resolve_pronunciation_layers_deterministically
FAILED tests/bdd/test_content_intelligence.py::test_reject_a_samepriority_pronunciation_conflict
FAILED tests/bdd/test_content_intelligence.py::test_preserve_repeated_negation_and_criticaltoken_occurrences
FAILED tests/bdd/test_content_intelligence.py::test_reject_a_provider_capability_that_would_split_a_complete_turn

6 failed in 0.22s

1) ModuleNotFoundError: No module named 'poddown.content'
2) AssertionError: poddown.content.service is not implemented
3) AssertionError: poddown.content.lexicon is not implemented
4) AssertionError: poddown.content.tokens is not implemented
5) AssertionError: poddown.content.segmentation is not implemented

Observed failure root cause remains missing poddown.content behavior, not malformed fixtures.
```

### Residual risks
- The test suite now relies on optional YAML parsing when `PyYAML` is present; fallback parser handles simple nested maps but not complex frontmatter constructs.
- Assertions are tightly coupled to fixture-defined expectations; subsequent fixture changes must maintain all expectation keys.

## Task 1 Fix Round 2 report

### Changed files
- `tests/bdd/test_content_intelligence.py`
- `tests/fixtures/content/robotics-adaptation.json`

### Commands run
- `cat .superpowers/sdd/2026-08-09-content-intelligence/task-1-review-round-2.md`
- `cd /Users/djh/Documents/ChatGPT/PodDown/.worktrees/m1-content-intelligence && rtk uv run pytest tests/bdd/test_content_intelligence.py -v`
- `cd /Users/djh/Documents/ChatGPT/PodDown/.worktrees/m1-content-intelligence && uv run pytest tests/bdd/test_content_intelligence.py -v` (blocked by environment approval policy)
- `git status --short`

### Exact focused validation output
```
FAILED tests/bdd/test_content_intelligence.py::test_prepare_a_difficult_twospeaker_technical_source
FAILED tests/bdd/test_content_intelligence.py::test_reject_a_changed_number_before_rendering
FAILED tests/bdd/test_content_intelligence.py::test_resolve_pronunciation_layers_deterministically
FAILED tests/bdd/test_content_intelligence.py::test_reject_a_samepriority_pronunciation_conflict
FAILED tests/bdd/test_content_intelligence.py::test_preserve_repeated_negation_and_criticaltoken_occurrences
FAILED tests/bdd/test_content_intelligence.py::test_reject_a_provider_capability_that_would_split_a_complete_turn

6 failed in 0.17s

1) ModuleNotFoundError: No module named 'poddown.content'
2) AssertionError: poddown.content.service is not implemented
3) AssertionError: poddown.content.lexicon is not implemented
4) AssertionError: poddown.content.lexicon is not implemented
5) AssertionError: poddown.content.tokens is not implemented
6) AssertionError: poddown.content.segmentation is not implemented
```

### Residual risks
- `_critical_token_identity` now depends on occurrence IDs for strict duplicate-safe assertion; if production `extract_critical_tokens` omits `occurrence_id`, the test will fail even if values are correct.
- `_span_bounds` asserts integer starts/ends; any float span representation will fail the contract.
- Renderer boundary is hard-bound to a `renderer` keyword and rejects alternate but equivalent names, by design.

## Task 1 Fix Round 3 report

### Changed files
- `tests/bdd/test_content_intelligence.py`

### Commands run
- `cat .superpowers/sdd/2026-08-09-content-intelligence/task-1-review-round-3.md`
- `cd /Users/djh/Documents/ChatGPT/PodDown/.worktrees/m1-content-intelligence && rtk uv run pytest tests/bdd/test_content_intelligence.py -v`

### Exact focused validation output
```
FAILED tests/bdd/test_content_intelligence.py::test_prepare_a_difficult_twospeaker_technical_source
FAILED tests/bdd/test_content_intelligence.py::test_reject_a_changed_number_before_rendering
FAILED tests/bdd/test_content_intelligence.py::test_resolve_pronunciation_layers_deterministically
FAILED tests/bdd/test_content_intelligence.py::test_reject_a_samepriority_pronunciation_conflict
FAILED tests/bdd/test_content_intelligence.py::test_preserve_repeated_negation_and_criticaltoken_occurrences
FAILED tests/bdd/test_content_intelligence.py::test_reject_a_provider_capability_that_would_split_a_complete_turn

6 failed in 0.15s

1) ModuleNotFoundError: No module named 'poddown.content'
2) AssertionError: poddown.content.service is not implemented
3) AssertionError: poddown.content.lexicon is not implemented
4) AssertionError: poddown.content.lexicon is not implemented
5) AssertionError: poddown.content.tokens is not implemented
6) AssertionError: poddown.content.segmentation is not implemented
```

### Residual risks
- `_span_bounds` now requires explicit `source_span_*` and `script_span_*` fields; any implementation using only generic span fields will fail this contract by design.
- `critical_tokens_have_spoken_forms` now requires exact emitted token order to match fixture order, which intentionally increases strictness for duplicate/near-duplicate tokens.
