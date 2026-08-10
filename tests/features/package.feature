Feature: Commit an immutable episode package
  A package is accepted only when its complete artifact set and final QA evidence
  are immutable, internally consistent, and safe to distribute.

  Scenario: Commit a complete QA-passed nine-file package
    Given a complete verified episode artifact set
    When the immutable episode package is committed
    Then the package contains the approved nine files in manifest order
    And every manifest file records the exact byte count and SHA-256 checksum

  Scenario: Reject a package with a missing approved artifact
    Given a verified episode artifact set missing "transcript.vtt"
    When the immutable episode package is committed
    Then package commit is rejected

  Scenario: Reject a package with a duplicate artifact name
    Given a verified episode artifact set with duplicate "episode.mp3" artifacts
    When the immutable episode package is committed
    Then package commit is rejected

  Scenario: Reject a package with an unsafe artifact name
    Given a verified episode artifact set containing unsafe name "../episode.wav"
    When the immutable episode package is committed
    Then package commit is rejected

  Scenario: Reject failed final QA evidence
    Given a complete episode artifact set with failed QA evidence
    When the immutable episode package is committed
    Then package commit is rejected

  Scenario: Reject incomplete critical-token accuracy
    Given a complete episode artifact set with critical-token accuracy 0.99
    When the immutable episode package is committed
    Then package commit is rejected

  Scenario: Serialize equivalent package manifests deterministically and safely
    Given two equivalent complete verified episode artifact sets
    When both immutable episode packages are committed
    Then their package manifests serialize identically
    And the manifest contains only approved schema fields

  Scenario: Replay an identical package commit without replacing its manifest
    Given a complete verified episode artifact set
    When the same immutable episode package is committed twice
    Then the replay returns the original immutable manifest

  Scenario: Reject a conflicting package commit without replacing the original
    Given a committed immutable episode package
    When a different package is committed for the same episode version
    Then the conflicting package commit is rejected
    And the original immutable manifest remains readable

  Scenario: Fail closed when stored artifact bytes are corrupted
    Given a stored immutable artifact
    When its stored bytes are corrupted
    Then reading the artifact fails integrity verification
