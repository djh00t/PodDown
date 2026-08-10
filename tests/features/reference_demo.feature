Feature: Reference episode demo

  Scenario: Complete the difficult technical dialogue in deterministic local mode
    Given an empty reference demo output directory
    When the reference episode demo is run
    Then the result records a validated source profile and two speakers
    And three takes are rendered for every segment with stable voice bindings
    And one failed segment is regenerated before QA
    And usage records the deliberate failed render invocation
    And final critical-token accuracy is 1.0
    And the package contains the nine required artifacts
    And publication is a filesystem demo publication
    And the MCP preview reports no side effect

    Scenario: Resume the completed reference episode from persisted evidence
      Given a completed reference episode demo
      When the reference episode demo is resumed
      Then the result reports replayed render takes
      And the persisted publication is scoped to its episode version
      And the package and publication identities are unchanged

    Scenario: Reject tampered persisted result evidence
      Given a completed reference episode demo with persisted evidence
      When the persisted result accuracy is changed
      Then resuming the reference episode fails closed

    Scenario: Complete the difficult technical dialogue with injected local speech
      Given an empty local speech reference demo output directory
      When the local speech reference episode demo is run
      Then the result records host-local speech provenance
      And three takes are rendered for every segment with stable voice bindings
      And one failed segment is regenerated before QA
      And the local speech renderer receives canonical pronunciation text

    Scenario: Reject a mismatched local-speech resume before dispatch
      Given a completed local speech reference episode demo
      When it is resumed with mismatched local renderer provenance
      Then local speech resume fails before renderer dispatch

    Scenario: Reject unsafe local-speech renderer evidence
      Given an empty local speech reference demo output directory
      When unsafe local speech renderers are run
      Then each unsafe local speech renderer fails before publication

    Scenario: Reject malformed local-speech renderer output before publication
      Given an empty local speech reference demo output directory
      When malformed local speech renderer output is run
      Then malformed local speech output fails before packaging and publication
