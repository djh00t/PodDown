Feature: PostgreSQL command receipts
  PostgreSQL is the authoritative durable boundary for workflow dispatch receipts.

  Scenario: A queued receipt replays and advances exactly once
    Given a recording PostgreSQL command receipt store
    When I reserve and dispatch a PostgreSQL command receipt
    Then the PostgreSQL receipt replays as dispatched

  Scenario: A receipt payload collision is rejected
    Given a recording PostgreSQL command receipt store
    When I reserve the same PostgreSQL receipt with a different payload
    Then the PostgreSQL receipt collision is rejected
