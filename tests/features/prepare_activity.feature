Feature: Prepare content activity
  The Temporal preparation boundary carries source-bound evidence without provider work.

  Scenario: Prepare valid source into deterministic references
    Given a valid preparation activity input
    When the content preparation activity runs
    Then it returns source, profile, script, segment, and critical-token references
    And it records zero provider calls

  Scenario: Reject a mismatched requested profile
    Given a preparation activity input with a mismatched profile
    When the content preparation activity runs
    Then it fails with a non-retryable preparation validation error

  Scenario: Preserve the immutable preparation input snapshot
    Given a valid preparation activity input
    Then the preparation activity input snapshot is immutable and JSON-safe
