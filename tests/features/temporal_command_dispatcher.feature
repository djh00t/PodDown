Feature: Temporal command dispatch
  Accepted API commands use deterministic workflow identities and durable receipts.

  Scenario: Dispatching an idempotent command starts one workflow
    Given a recording Temporal command transport
    When I submit the same render command twice
    Then the transport receives one deterministic workflow start
    And both receipts identify the dispatched workflow

  Scenario: Publication command payload is preserved for the workflow
    Given a recording Temporal command transport
    When I submit a publish command with a target and approval
    Then the workflow payload contains the publication scope
