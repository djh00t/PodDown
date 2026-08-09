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
