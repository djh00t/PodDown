Feature: release evidence gate
  Release readiness requires complete, traceable security and build evidence.

  Scenario: a complete release evidence bundle is eligible
    Given a complete release evidence bundle
    When the release gate is evaluated
    Then the release evidence is ready

  Scenario: skipped security evidence blocks release
    Given a release evidence bundle with a skipped security check
    When the release gate is evaluated
    Then the release evidence is blocked with the missing check
