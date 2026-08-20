Feature: Source-bound reasoning requests
  Reasoning requests must carry immutable source identity and bounded treatment data.

  Scenario: Build a strict adaptation request from a source snapshot
    Given a source snapshot and approved dialogue treatment
    When I build a reasoning adaptation request
    Then the request contains the source hash and treatment identity
    And the request contains no provider credential

