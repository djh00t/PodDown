Feature: Guarded live provider runtime construction

  Scenario: Construct live providers only from complete explicit configuration
    Given a complete live provider runtime environment
    When the live provider runtime is constructed
    Then the runtime exposes the pinned live route and mapped voices

  Scenario: Reject live runtime configuration without voice consent evidence
    Given a live provider runtime environment without consent evidence
    When I attempt to construct the live provider runtime
    Then live runtime construction fails before provider dispatch

  Scenario: Reject live runtime configuration without explicit enablement
    Given a complete live provider runtime environment
    When live provider enablement is removed
    Then live runtime construction fails before provider dispatch
