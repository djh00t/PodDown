Feature: Live structured adaptation boundary
  Live reasoning must be explicit, source-bound, and evidence-bearing.

  Scenario: Adaptation uses an injected Responses transport
    Given a live adaptation service with a valid provider response
    When I run live adaptation
    Then the adapted script preserves the source hash
    And one reasoning evidence record is produced

  Scenario: Non-normalized reasoning usage fails closed
    Given a live adaptation service with non-normalized provider usage
    When I run live adaptation
    Then live adaptation fails with a provider metering error
