Feature: Durable publication approvals
  A publication approval is scoped, auditable and consumed exactly once.

  Scenario: A valid approval is consumed once
    Given a durable SQLite approval repository
    When I issue and consume a publish approval twice
    Then the first consumption succeeds and the replay is rejected

  Scenario: An approval cannot cross tenant or project scope
    Given a durable SQLite approval repository
    When I consume an approval from another project
    Then approval consumption is rejected

  Scenario: An expired approval fails closed
    Given a durable SQLite approval repository
    When I consume an expired approval
    Then approval consumption is rejected
