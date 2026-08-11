Feature: Production API contract models
  The API contract carries production workflow status without exposing raw failures.

  Scenario: Status includes production references and safe structured failure
    Given a production episode status model
    Then it exposes only allowlisted failure details and production references
