Feature: durable provider-bound Temporal render activity
  The provider-neutral activity keeps deterministic local render evidence durable
  while failing closed before provider dispatch when rights do not match.

  Scenario: durable activity persists one candidate and one cost event
    Given a consented deterministic local render segment
    When the provider-bound Temporal activity runs
    Then the renderer is called once and the returned candidate passes local QA
    And exactly one immutable artifact and one cost event are persisted

  Scenario: retry after persistence replays without duplicate provider usage
    Given a consented deterministic local render segment
    And the first activity invocation fails after durable persistence
    When the Temporal workflow retries the same activity key
    Then the workflow completes
    And the renderer is called once for that candidate
    And the cost event count remains one

  Scenario: invalid consent fails before provider dispatch
    Given a deterministic local render segment without matching provider consent
    When the provider-bound Temporal activity runs
    Then the activity fails with a non-retryable rights error
    And the renderer is never called

  Scenario: hosted render without provider QA fails before dispatch
    Given a consented hosted render segment without a quality evaluator
    When the provider-bound Temporal activity runs
    Then the activity fails with a non-retryable workflow contract error
    And the renderer is never called

  Scenario: invalid consent terminates the episode without repair
    Given a deterministic local render segment without matching provider consent
    When the Temporal episode workflow runs with invalid consent
    Then the workflow fails on its first attempt with a rights error
    And no provider dispatch or durable cost record exists

  Scenario: a passing take survives a sibling transient failure
    Given a consented deterministic local render segment
    And the third take is configured to exhaust transient retries
    When the Temporal episode workflow runs with one repair attempt
    Then a passing take is accepted without segment repair
    And the transient take is excluded from provider usage

  Scenario: unknown terminal activity failure reports an activity gate
    Given a consented deterministic local render segment
    When the Temporal episode workflow runs with an unknown terminal activity failure
    Then the workflow fails on its first attempt with an activity gate
    And no provider dispatch or durable cost record exists

  Scenario: matching injected transcription returns provenance
    Given a consented segment with an injected matching transcriber
    When the provider-bound activity runs with the injected transcriber
    Then the returned quality includes transcription provenance

  Scenario: missing critical token produces segment rerender evidence
    Given a consented segment with an injected transcriber missing a critical token
    When the provider-bound activity runs with the injected transcriber
    Then the returned quality requires segment rerender

  Scenario: malformed non-retryable transcription fails closed
    Given a consented segment with a malformed non-retryable transcriber
    When the provider-bound activity runs with the injected transcriber
    Then transcription fails closed without canonical-text fallback

  Scenario: retryable transcription exhaustion reports a transcription gate
    Given a consented segment with a retryable failing transcriber
    When the Temporal episode workflow runs with retryable transcription failures
    Then the workflow fails with a transcription gate after bounded retries

  Scenario: durable quality replay avoids a second transcription dispatch
    Given a consented segment with an injected matching transcriber
    When the provider-bound activity runs twice with the same durable quality key
    Then transcription dispatch count remains one

  Scenario: deterministic local mode is explicit and zero-cost
    Given a consented deterministic local render segment
    When the provider-bound Temporal activity runs
    Then the quality evidence is explicitly deterministic local and zero-cost
