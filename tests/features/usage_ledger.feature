Feature: Durable usage and cost ledger
  Provider usage is immutable, tenant scoped, and safe to replay.

  Scenario: A provider request replay retains arbitrary-precision cost evidence
    Given a durable usage event with estimated and reconciled costs
    When the same provider request is recorded twice
    Then one tenant-scoped usage event retains its exact Decimal costs
