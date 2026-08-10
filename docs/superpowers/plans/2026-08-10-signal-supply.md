# Signal & Supply Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Prove a domain-neutral Signal & Supply customer fixture through existing PodDown content, rights, and publishing contracts with deterministic offline evidence.

**Architecture:** Store a versioned show profile, source article, adaptation proposal, approved synthetic voice metadata/consents, pronunciation lexicons, disclosure policy, publishing target, and workflow configuration under an integration fixture directory. Tests load these artifacts and call public `poddown.content.service.prepare_content` and public publishing value objects; no PodDown core module changes or finance logic are introduced.

**Tech Stack:** YAML/JSON/Markdown fixtures, Python 3.12, pytest-bdd, pytest, existing content and publishing contracts, and offline deterministic fakes.

## Global Constraints

- PodDown core contains no ticker, market, portfolio, investment, or Signal & Supply logic.
- Finance/technology critical tokens must achieve exactly 100% fidelity.
- Counter-thesis and uncertainty must remain source-bound without hype or invented facts.
- Synthetic-presenter disclosure must be present according to profile policy.
- The existing robotics fixture must render unchanged.
- Synthetic/demo assets are clearly labeled and no live providers, credentials, or spend are used.

---

### Task 1: Define fixture acceptance with BDD and evals

**Files:**
- Create: `tests/features/signal_supply.feature`
- Create: `tests/bdd/test_signal_supply.py`
- Create: `tests/unit/test_signal_supply_fixtures.py`
- Create: `tests/integration/test_signal_supply.py`
- Create: `tests/evals/test_signal_supply.py`

- [ ] Add scenarios for public-contract preparation, 100% critical-token fidelity, counter-thesis/uncertainty preservation, disclosure, unchanged robotics regression, and configuration removal leaving no finance core references.
- [ ] Load only fixture files and public APIs; invoke the deterministic local renderer through `DurableRenderService.render_takes` and evaluate the fixture transcript with `poddown.qa.fidelity.evaluate_critical_tokens`.
- [ ] Run the focused suite and capture the expected RED failure because the M6 fixture does not exist.

### Task 2: Add versioned Signal & Supply integration artifacts

**Files:**
- Create: `integrations/signal-supply/v1/show-profile.yaml`
- Create: `integrations/signal-supply/v1/article.md`
- Create: `integrations/signal-supply/v1/adaptation.json`
- Create: `integrations/signal-supply/v1/voices.yaml`
- Create: `integrations/signal-supply/v1/lexicons.yaml`
- Create: `integrations/signal-supply/v1/disclosure.yaml`
- Create: `integrations/signal-supply/v1/publishing-target.yaml`
- Create: `integrations/signal-supply/v1/github-workflow.yaml`
- Create: `integrations/signal-supply/v1/evals.json`
- Create: `integrations/signal-supply/v1/spoken-transcript.txt`

- [ ] Keep the article synthetic and source-bound, including explicit uncertainty and counter-thesis language.
- [ ] Record opaque demo voice asset IDs and valid consents without provider identifiers or credentials.
- [ ] Define finance/technology pronunciations and critical-token expectations without ticker/market logic in code.
- [ ] Define disclosure, provider-neutral publication metadata, and protected workflow configuration as data only.
- [ ] Keep the transcript deterministic and explicit about local-demo rendering; it is not provider or live audio evidence.

### Task 3: Verify, document, and deliver

**Files:**
- Create: `docs/verification/signal-supply.md`

- [ ] Run focused BDD/unit/integration/eval tests and changed-scope `make check`.
- [ ] Run `make build`, `make docs`, `uv lock --check`, `uv pip check`, compileall, diff, and credential checks.
- [ ] Confirm core source files are unchanged and remove generated artifacts.
- [ ] Commit one review-ready Conventional Commit locally; do not push or create a PR.
