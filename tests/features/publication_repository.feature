Feature: Durable publication receipt repository
  The local repository records only scoped publication evidence and never calls a provider.

  Scenario: Replay an exact successful receipt after restart
    Given a completed publication receipt recorded locally
    When I reconstruct the publication receipt repository and begin the same publication
    Then the recorded successful receipt is replayed exactly

  Scenario: Reuse one idempotency key across independent publication scopes
    Given a completed publication receipt recorded locally
    When I begin a different scoped publication with the same idempotency key
    Then both scoped publication attempts are retained independently

  Scenario: Reject target drift within one publication scope
    Given a completed publication receipt recorded locally
    When I begin the same scoped publication with a changed target snapshot
    Then the publication repository fails closed

  Scenario: Hold an uncertain provider outcome for review
    Given a pending publication attempt recorded locally
    When I record an uncertain provider outcome
    Then the same publication cannot begin another provider attempt

  Scenario: Reject a corrupted receipt during successful replay
    Given a completed publication receipt recorded locally
    When the persisted successful receipt has a stale external reference
    Then the publication repository rejects the successful replay

  Scenario: Reject a stale pending attempt handle
    Given a pending publication attempt recorded locally
    When I use a stale publication attempt handle
    Then the stale attempt handle cannot change the publication outcome

  Scenario: Redact known publication failure evidence
    Given a pending publication attempt recorded locally
    When I record a known provider failure containing sensitive evidence
    Then the retained failure evidence contains no raw secret

  Scenario: Roll back a successful attempt when receipt persistence fails
    Given a pending publication attempt recorded locally
    When receipt persistence is forced to fail during success recording
    Then the publication remains pending without a receipt
