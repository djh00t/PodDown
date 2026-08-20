Feature: PostgreSQL episode persistence
  PostgreSQL is the authoritative tenant-scoped episode repository.

  Scenario: Exact source bytes survive a PostgreSQL replay
    Given a recording PostgreSQL episode repository
    When I create and replay a PostgreSQL episode
    Then the exact source bytes and identity are preserved

  Scenario: PostgreSQL episode replacement rejects a stale version
    Given a recording PostgreSQL episode repository
    When I replace the same PostgreSQL episode twice with one expected version
    Then the stale PostgreSQL replacement is rejected
