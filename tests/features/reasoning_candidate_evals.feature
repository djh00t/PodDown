Feature: Evaluate deterministic reasoning candidates
  Configured model fixtures are evaluated without a live provider and the
  cheapest candidate that preserves the source is selected.

  Scenario: Select the cheapest source-faithful candidate
    Given deterministic source-fidelity candidate fixtures
    When the reasoning candidates are evaluated
    Then the cheapest passing candidate is selected with stable evidence

  Scenario: Reject a candidate that changes negation
    Given a candidate fixture that changes a source negation
    When the reasoning candidates are evaluated
    Then the negation gate fails and no candidate is selected

  Scenario: Select a production reasoning configuration from deterministic evidence
    Given deterministic source-fidelity candidate fixtures
    When the production reasoning configuration is selected
    Then the cheapest passing production model has secret-free provenance
