Feature: Durable provider evidence and cost persistence
  Provider metadata and its usage event are one replay-safe evidence boundary.

  Scenario: A provider evidence and usage pair survives a restart
    Given a complete provider evidence and matching usage event
    When the provider evidence pair is persisted and the ledger is reconstructed
    Then the same evidence and usage event replay without duplication

  Scenario: A conflicting provider evidence replay fails closed
    Given a complete provider evidence and matching usage event
    When the same provider request is replayed with a different output digest
    Then the provider evidence conflict is rejected
