Feature: Restart-safe production command idempotency
  The packaged deterministic-local pipeline reconstructs its API, dispatcher,
  repository, and worker runtime from durable local state without a Temporal,
  Postgres, MinIO, or provider service.

  Scenario: Restart replays a dispatched render receipt without another workflow start
    Given a locally dispatched render command in packaged deterministic-local runtime
    When I reconstruct the packaged runtime from the same local state and replay the render command
    Then the original dispatched receipt and workflow identity are returned without another workflow start
    And the authoritative episode status remains available after restart
    And a conflicting render identity is rejected without another workflow start
    And the local worker contract set survives reconstruction

  Scenario: Restart submits a queued render reservation after a crash before Temporal acceptance
    Given a queued render reservation survives a crash before Temporal accepts it
    When I restart and Temporal accepts the queued render workflow
    Then the queued render receives two attempts and one accepted stable workflow identity
    And the already-started queued receipt is reconciled as dispatched

  Scenario: Restart reconciles a queued render reservation after a crash following Temporal acceptance
    Given a queued render reservation survives a crash after Temporal accepts it
    When I restart and Temporal reports the queued render workflow already started
    Then recovery reconciles the accepted render without a second accepted start

  Scenario: A tampered durable workflow identity fails closed before transport
    Given a queued durable render receipt has a tampered workflow identity
    When I submit the matching render command through a reconstructed dispatcher
    Then the tampered receipt is rejected without a workflow start or state transition

  Scenario: Package completion stays idempotent and incomplete state never completes
    Given a completed package candidate, a failing commit, and an incomplete candidate
    When I replay the completed package, commit the failing package, and reject the incomplete candidate
    Then the completed package is replayed unchanged
    And the failed tentative package commit has no completed record after reconstruction
    And the package completion retry is replayed unchanged
    And the incomplete package candidate has no completed record in a fresh repository
