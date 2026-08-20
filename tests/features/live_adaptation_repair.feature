Feature: Bounded adaptation repair
  Only dialogue-quality failures may receive one schema-preserving repair.

  Scenario: Repair preserves the original turn identity and anchors
    Given a live adaptation service with a valid repair response
    When I repair one dialogue-quality turn
    Then the repaired script preserves the protected turn identity

