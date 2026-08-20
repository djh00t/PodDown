Feature: service-level objective contracts
  SLO measurements use bounded numeric values and never carry source or provider
  payloads.

  Scenario: an availability objective is met
    Given an availability SLO objective
    When the observed availability is 0.999
    Then the SLO measurement is met

  Scenario: a queue-age objective is missed
    Given a queue-age SLO objective
    When the observed queue age is 91 seconds
    Then the SLO measurement is not met
