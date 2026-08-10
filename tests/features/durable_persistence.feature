Feature: Durable local episode persistence and usage metering
  The deterministic local adapter survives process restart without cloud
  services, credentials, or provider calls.

  Scenario: Restart preserves an episode and command receipt
    Given an episode and command receipt persisted locally
    When I reconstruct the local persistence adapters
    Then the episode and command receipt survive restart

  Scenario: Restart preserves one provider usage event
    Given a provider usage event persisted locally
    When I reconstruct the local persistence adapters
    Then the exact usage event replays without duplication

  Scenario: Local persistence remains provider-free
    Given an empty local persistence file
    Then the durable adapters require no external service

  Scenario: Keep episode and command idempotency tenant-scoped
    Given an episode and command receipt persisted locally
    When I replay the episode and command with the same identity
    Then the original records are returned

  Scenario: Reject conflicting episode and command replays
    Given an episode and command receipt persisted locally
    When I replay them with conflicting identity
    Then both conflicts fail closed

  Scenario: Reject stale lifecycle versions
    Given an episode and command receipt persisted locally
    When I submit the same lifecycle update twice
    Then the stale update fails with a version conflict

  Scenario: Hide an episode from another tenant
    Given an episode and command receipt persisted locally
    When another tenant looks up the episode
    Then the lookup is not found

  Scenario: Reject conflicting provider usage replay
    Given a provider usage event persisted locally
    When I replay the provider request with a different cost
    Then the usage conflict fails closed
