Feature: PostgreSQL provider evidence ledger
  Provider evidence and usage are one tenant-scoped durable transaction.

  Scenario: Evidence and usage replay without duplication
    Given a recording PostgreSQL evidence ledger connection
    When I record the same provider evidence pair twice
    Then the evidence and usage are inserted once and replay identically

  Scenario: A partial pair fails closed
    Given a recording PostgreSQL evidence ledger connection
    When the usage row exists without provider evidence
    Then recording the pair rejects the inconsistent state
