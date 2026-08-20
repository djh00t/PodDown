Feature: bounded load admission
  Local admission decisions are deterministic and cost-aware.

  Scenario: an episode is admitted within capacity limits
    Given a bounded load contract
    When one episode is checked with safe queue age and cost
    Then the load decision admits the episode

  Scenario: an episode is rejected when concurrency is exhausted
    Given a bounded load contract
    When the concurrency limit is already full
    Then the load decision rejects the episode with a bounded reason
