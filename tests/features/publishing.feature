Feature: Publish verified immutable episode packages
  Scenario: Publish only an authorized QA-passed package
    Given a verified QA-passed package and an authorized publishing target
    When I publish the package
    Then the publication receipt is immutable and provenance-bound

  Scenario: Reject a package that is not QA-passed
    Given a package whose QA evidence failed
    When I attempt publication
    Then publication is rejected before any adapter call

  Scenario: Replay one idempotent publication
    Given a verified QA-passed package and an authorized publishing target
    When I publish the package twice with one idempotency key
    Then both calls return the same publication receipt

  Scenario: Render deterministic RSS without duplicate entries
    Given a verified QA-passed package and an authorized publishing target
    When I publish the package to RSS twice
    Then the RSS bytes are valid and identical

  Scenario: Reject update or delete without separate authorization
    Given an existing publication
    When I request an update or delete without separate authorization
    Then the publication change is rejected
