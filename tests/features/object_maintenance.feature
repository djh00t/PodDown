Feature: Durable object maintenance
  Object cleanup must remain tenant-scoped and delete only stale, unreferenced objects.

  Scenario: a worker runs one explicit conservative cleanup pass
    Given an explicit object maintenance activity
    When the object maintenance command runs
    Then the activity returns the scoped cleanup report

  Scenario: malformed object maintenance input is rejected
    Given an explicit object maintenance activity
    When the object maintenance command has an invalid grace period
    Then the object maintenance activity rejects the command
