Feature: Structured reasoning provider metering
  Reasoning evidence must preserve provenance and normalized usage without payload text.

  Scenario: Live reasoning evidence records normalized usage and cost
    Given a validated live reasoning response
    When I record structured reasoning evidence
    Then the evidence is provider-live with the response cost

