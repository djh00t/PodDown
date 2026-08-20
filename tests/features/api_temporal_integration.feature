Feature: API to Temporal dispatch

  Scenario: An API-dispatched create command runs on a local Temporal worker
    Given a source-bound deterministic render fixture
    When the API command runs against a local Temporal worker
    Then the dispatched Temporal workflow completes the render snapshot
