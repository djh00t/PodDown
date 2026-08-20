Feature: Atomically complete a mastered episode package
  Package object references and terminal completion are one immutable commit
  after final-master QA has validated the exact WAV and MP3 outputs.

  Scenario: Replay one validated package completion without partial status
    Given final-master QA evidence and package object references
    When the package completion is committed twice
    Then the same completed package record is returned

  Scenario: Reject a package completion when the atomic commit fails
    Given final-master QA evidence and a failing package completion repository
    When the package completion is committed
    Then no completed package record exists

  Scenario: Reject incomplete object metadata at the package-completion boundary
    Given final-master QA evidence and package object references
    When a package object reference has empty schema-version metadata
    Then the metadata rejection is a package completion error
