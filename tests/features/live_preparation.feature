Feature: live provider content preparation
  Live reasoning must feed the same source-bound preparation gates as local runs.

  Scenario: live adaptation is used for canonical preparation
    Given a source-bound live preparation request and recording adapter
    When I prepare content through the live adapter
    Then the live adapter was called once
    And the prepared manifest records one provider call
    And the prepared script remains source-bound

  Scenario: live preparation does not fall back to fixture reasoning
    Given a source-bound live preparation request and failing adapter
    When live preparation is attempted
    Then live preparation fails without a fixture fallback
