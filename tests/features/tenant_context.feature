Feature: Transaction-scoped PostgreSQL tenant context
  PostgreSQL RLS receives an authenticated tenant only within one DB-API
  transaction, while local SQLite and test boundaries make no RLS claim.

  Scenario: PostgreSQL context is parameterized before tenant work and clears on reuse
    Given a reusable PostgreSQL DB-API connection
    When I run tenant work for two tenants in separate transactions
    Then each transaction sets one parameterized local tenant context before its work
    And the reused connection commits before the next tenant context begins

  Scenario: Local test storage has no PostgreSQL RLS context
    Given a local SQLite DB-API connection
    When I run tenant work in the local transaction boundary
    Then the local boundary does not set a PostgreSQL tenant context

  Scenario: A database-kind override cannot bypass connection validation
    Given a test DB-API connection
    When I request a PostgreSQL tenant context on the test connection
    Then the tenant context rejects the mismatched database kind
