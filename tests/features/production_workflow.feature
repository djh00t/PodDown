Feature: Complete episode production workflow
  Production stages must run from one immutable snapshot and expose checksum-bound evidence.

  Scenario: Run every required production stage
    Given a valid complete production workflow snapshot
    When the production workflow runs with deterministic stage adapters
    Then it completes with a package and manifest digest
    And it invokes the stages in dependency order
    And it keeps preparation evidence bounded at stage boundaries

  Scenario: Reject incomplete final-master QA
    Given a valid complete production workflow snapshot
    When the production workflow returns below-perfect final-master accuracy
    Then the production workflow fails with a non-retryable stage-output error
