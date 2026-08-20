Feature: Fail-closed adaptation retry policy
  Unsupported claims and policy failures must not be retried.

  Scenario: Unsupported claims are terminal
    Given a live adaptation service with an unsupported-claim response
    When I run live adaptation
    Then live adaptation fails without a repair dispatch

