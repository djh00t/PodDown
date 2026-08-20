Feature: Temporal publication activity
  Publication commands invoke one explicit, idempotent activity over a worker-resolved immutable package reference.

  Scenario: a publish command runs the durable publication activity
    Given a verified package and configured publication activity
    When the Temporal publish command runs
    Then the workflow returns one publication receipt
    And the published filesystem contains the exact episode bytes
