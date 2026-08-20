Feature: Durable command jobs and publication approvals
  Durable repository seams preserve idempotency and approval boundaries across
  process restarts without calling a provider or an API boundary.

  Scenario: A command job replays after restart
    Given a persisted command job repository
    When the command job repository is reconstructed
    Then the same command request returns its original workflow job

  Scenario: A command replay returns current transition state after restart
    Given a persisted command job repository
    When the command job transitions and the repository is reconstructed
    Then the replay returns the current command job state

  Scenario: A command job rejects a conflicting replay
    Given a persisted command job repository
    When the command request is replayed with a different workflow identity
    Then the command replay fails safely

  Scenario: A command job is hidden from another tenant
    Given a persisted command job repository
    When another tenant replays the command request
    Then the other tenant receives a distinct command job

  Scenario: An approval is consumed once in its publication scope
    Given a persisted scoped publication approval
    When the approval is consumed twice
    Then only the first approval consumption succeeds

  Scenario: An approval requires complete scoped evidence
    Given a persisted scoped publication approval
    When the approval is consumed with a different operation
    Then the approval is unavailable

  Scenario: An expired approval is unavailable
    Given an expired scoped publication approval
    When the approval is consumed with its complete evidence
    Then the approval is unavailable

  Scenario: Approval evidence rejects malformed identifiers
    Given a publication approval repository
    When malformed approval evidence is recorded
    Then the malformed approval evidence is rejected

  Scenario: Concurrent approval consumption has one winner
    Given a persisted scoped publication approval
    When two callers consume the approval concurrently
    Then exactly one approval consumption succeeds
