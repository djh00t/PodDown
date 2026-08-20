Feature: PostgreSQL publication approvals
  Publication approvals are tenant-scoped, immutable, and consumed once.

  Scenario: A PostgreSQL approval survives issue and one-time consumption
    Given a recording PostgreSQL approval repository
    When I issue and consume a PostgreSQL approval
    Then the PostgreSQL approval is consumed with its audit timestamp

  Scenario: A PostgreSQL approval rejects a cross-project consume
    Given a recording PostgreSQL approval repository
    When I issue and consume the approval from another project
    Then the PostgreSQL approval consume is rejected
