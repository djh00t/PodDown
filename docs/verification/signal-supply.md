# Signal & Supply integration verification

## Scope

The versioned `integrations/signal-supply/v1/` fixture contains a synthetic
finance/technology article, source-bound adaptation proposal, show profile,
approved demo-only synthetic voice assets and consents, pronunciation lexicon,
disclosure policy, provider-neutral publishing target, protected workflow
configuration, and deterministic eval expectations.

The integration constructs the typed `ContentPreparationRequest` path with the
fixture profile's two speakers, declared voice assets/consents, source-bound
turn anchors, and an episode-scoped `PronunciationLexicon`. It calls public
`poddown.content.service.prepare_content(request=...)`,
`snapshot_source`, `extract_critical_tokens`, and publishing value objects. It
also invokes `DurableRenderService.render_takes` with the public
`DeterministicLocalRenderer` contract and passes the versioned deterministic
spoken transcript through `poddown.qa.fidelity.evaluate_critical_tokens`.
It does not modify PodDown core or add finance, ticker, market, portfolio, or
investment logic. It makes no live provider calls and contains no credentials.

## Acceptance evidence

Focused gate:

```text
PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
uv run pytest -p pytest_bdd.plugin -q \
  tests/bdd/test_signal_supply.py \
  tests/unit/test_signal_supply_fixtures.py \
  tests/integration/test_signal_supply.py \
  tests/evals/test_signal_supply.py
15 passed
```

The review-fix RED run first failed on missing adapted spoken text and then on
fidelity scores below 1.0 before the typed request, episode lexicon, declared
voice mapping, and deterministic transcript path were completed. The final
15-test gate covers
public-contract preparation, source hashing, critical-token fidelity, counter-
thesis and uncertainty preservation, disclosure, approved synthetic assets,
publishing target construction, robotics digest regression, and core isolation.
The rendering/fidelity assertions verify one local renderer call, the `local`
provider marker, and exact `accuracy == 1.0` from the real public evaluator
rather than relying on the eval metadata score.
The robotics scenario now prepares and renders its typed request through the
same local contract and checks the unchanged source digest.

The robotics fixture baseline is SHA-256
`ebe2aa14610aafad0fdca688ec156b321a7ae15d5bd98752a9b9f75e318b5c1e`.

## Deferrals and residual risks

- Voice assets are metadata-only synthetic demo records; no provider identity,
  registration, live call, or spend is established.
- The publishing target is provider-neutral configuration with a secret
  reference, not a publication or deployment claim.
- The workflow file is protected demo configuration only; coordinator-owned
  repository workflow wiring may be required for a real customer-one pipeline.
- Finance-specific production content governance and authenticated UAT remain
  outside this offline fixture slice.
- The transcript is deterministic local evidence, not live transcription; no
  hosted rendering, transcription, publication, deployment, or customer UAT is
  established.
