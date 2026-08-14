Feature: Production API contract models
  The API contract carries production workflow status without exposing raw failures.

  Scenario: Status includes production references and safe structured failure
    Given a production episode status model
    Then it exposes only allowlisted failure details and production references

  Scenario: Render cost ceiling uses the frozen decimal-string wire contract
    Given a JSON render request with a decimal-string cost ceiling
    When numeric JSON cost ceiling is submitted
    Then the string ceiling is preserved and the numeric ceiling is rejected
