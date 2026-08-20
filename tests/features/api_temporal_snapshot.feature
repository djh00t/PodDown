Feature: Source-bound API Temporal dispatch
  API commands must carry an immutable snapshot bound to the durable episode.

  Scenario: API binds the source snapshot before starting Temporal
    Given an API with a source-bound workflow snapshot factory
    When the API creates an episode for Temporal dispatch
    Then the Temporal payload contains the matching source snapshot

  Scenario: API rejects a snapshot for another source
    Given an API with a mismatched workflow snapshot factory
    When the API creates an episode for Temporal dispatch
    Then the API reports an unavailable workflow snapshot
