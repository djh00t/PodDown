Feature: PostgreSQL production schema contract
  Durable tables are tenant-owned and migrations are forward-only.

  Scenario: Apply the schema migrations idempotently
    Given a recording PostgreSQL migration connection
    When I apply the PodDown migrations twice
    Then each migration is recorded once in version order
    And tenant RLS policies are present in the schema

  Scenario: Set a transaction-local tenant context
    Given a recording PostgreSQL migration connection
    When I set the tenant context for a UUIDv7 tenant
    Then the connection receives a transaction-local tenant setting

