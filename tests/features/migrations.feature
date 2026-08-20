Feature: Forward-only database migrations
  The production data plane records each approved schema change so a restart
  cannot apply the same change twice.

  Scenario: Ordered migrations apply once across a restart
    Given an empty database and two ordered migrations
    When I run the migrations twice
    Then each migration is recorded once and its schema change remains

  Scenario: Out-of-order migration history fails closed
    Given a migration ledger with history out of order
    When I run migrations against the ledger
    Then migration execution fails without schema changes
