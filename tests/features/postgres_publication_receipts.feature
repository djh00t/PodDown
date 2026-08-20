Feature: PostgreSQL publication receipts
  Publication outcomes are durable, tenant-scoped, and replay-safe.

  Scenario: A publication receipt survives durable save and replay
    Given a recording PostgreSQL publication receipt repository
    When I save and replay a publication receipt
    Then the replayed receipt preserves its immutable audit evidence

  Scenario: A publication receipt rejects an idempotency conflict
    Given a recording PostgreSQL publication receipt repository
    When I save a conflicting publication receipt
    Then the publication idempotency conflict is rejected
