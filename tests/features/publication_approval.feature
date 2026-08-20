Feature: Durable publication approval consumption
  Publication operations require one scoped approval that can be consumed only once.

  Scenario: Consume an approval once for its exact publication scope
    Given an unconsumed publication approval
    When I consume it for its exact scope and publish operation
    Then the approval is marked consumed without storing its raw nonce
    And a second consume attempt is rejected

  Scenario: Reject a consume request for a different project scope
    Given an unconsumed publication approval
    When I consume it with a different project scope
    Then the consume request is rejected

  Scenario: Reject a consume request for a different episode scope
    Given an unconsumed publication approval
    When I consume it with a different episode scope
    Then the consume request is rejected

  Scenario: Reject a consume request for a different actor
    Given an unconsumed publication approval
    When I consume it with a different actor
    Then the consume request is rejected

  Scenario: Reject a consume request for a different nonce
    Given an unconsumed publication approval
    When I consume it with a different nonce
    Then the consume request is rejected

  Scenario: Reject a consume request for a different operation
    Given an unconsumed publication approval
    When I consume it for the update operation
    Then the consume request is rejected

  Scenario: Do not reveal consumed state to a mismatched replay
    Given a consumed publication approval
    When I retry it with a different tenant scope
    Then the mismatch does not reveal consumed or expired state

  Scenario: Do not reveal expired state to a mismatched request
    Given an expired publication approval
    When I consume it after expiry with a different tenant scope
    Then the mismatch does not reveal consumed or expired state

  Scenario: Do not reveal malformed lifecycle state to a mismatched request
    Given an unconsumed publication approval
    When I consume it with a different tenant scope after corrupting persisted lifecycle data
    Then the mismatch does not reveal malformed lifecycle state

  Scenario: Reject an approval before it is issued
    Given an unconsumed publication approval
    When I consume it before its issue time
    Then the approval validity is rejected

  Scenario: Reject an expired approval
    Given an unconsumed publication approval
    When I consume it after its expiry time
    Then the approval validity is rejected

  Scenario: Reject an invalid publication operation
    When I create an approval with an invalid operation
    Then publication approval validation is rejected

  Scenario: Reject malformed runtime UUID values
    When I create an approval with malformed runtime UUID values
    Then publication approval validation is rejected

  Scenario: Persist one-time consumption across repository restart
    Given an unconsumed publication approval
    When I restart the repository and consume the approval
    Then the consumed state survives a repository restart

  Scenario: Allow exactly one concurrent consumer
    Given an unconsumed publication approval
    When two consumers race to consume the approval
    Then exactly one concurrent consumer succeeds
