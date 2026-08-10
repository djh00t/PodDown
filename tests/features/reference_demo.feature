Feature: Reference episode demo

  Scenario: Complete the difficult technical dialogue in deterministic local mode
    Given an empty reference demo output directory
    When the reference episode demo is run
    Then the result records a validated source profile and two speakers
    And three takes are rendered for every segment with stable voice bindings
    And one failed segment is regenerated before QA
    And final critical-token accuracy is 1.0
    And the package contains the nine required artifacts
    And publication is a filesystem demo publication
    And the MCP preview reports no side effect

    Scenario: Resume the completed reference episode from persisted evidence
      Given a completed reference episode demo
      When the reference episode demo is resumed
      Then the result reports replayed render takes
      And the package and publication identities are unchanged

    Scenario: Reject tampered persisted result evidence
      Given a completed reference episode demo with persisted evidence
      When the persisted result accuracy is changed
      Then resuming the reference episode fails closed
