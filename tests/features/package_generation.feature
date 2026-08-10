Feature: Generate deterministic episode package artifacts
  A verified immutable episode version generates only evidence-bound package files.

  Scenario: Generate all nine artifacts from passing final-master QA
    Given a passing package-generation request
    When package artifacts are built
    Then all nine canonical package artifacts contain the expected deterministic bytes

  Scenario: Generate deterministic transcript cues and chapters
    Given a passing package-generation request
    When package artifacts are built twice
    Then the transcript VTT contains one monotonic three-decimal cue per word
    And chapters retain segment order and cumulative estimated durations

  Scenario: Include reproducible provenance and render-manifest evidence
    Given a passing package-generation request
    When package provenance and artifacts are built
    Then provenance and the render manifest contain sorted source-to-QA evidence

  Scenario: Reject failed final-master QA
    Given a package-generation request with failed final-master QA
    When package artifacts are built
    Then package generation fails closed

  Scenario: Reject missing transcript timestamps
    Given a package-generation request with no transcript words
    When package artifacts are built
    Then package generation fails closed

  Scenario: Reject invalid transcript timestamps
    Given a package-generation request with non-monotonic transcript timestamps
    When package artifacts are built
    Then package generation fails closed

  Scenario: Reject mismatched final-master checksums
    Given a package-generation request with a mismatched master checksum
    When package artifacts are built
    Then package generation fails closed

  Scenario: Reject non-JSON workflow evidence
    Given a package-generation request with non-JSON render evidence
    When package artifacts are built
    Then package generation fails closed

  Scenario: Reject incomplete critical-token accuracy
    Given a package-generation request with incomplete critical-token accuracy
    When package artifacts are built
    Then package generation fails closed

  Scenario: Commit and replay the generated package
    Given a passing package-generation request
    When generated package artifacts are committed and replayed
    Then the committed package manifest and bytes replay identically

  Scenario: Replay equivalent immutable inputs byte-for-byte
    Given two equivalent package-generation requests
    When package artifacts are built for both requests
    Then every generated artifact payload is byte-identical
