Feature: Manage an offline tenant-scoped episode lifecycle
  PodDown snapshots validated source before work and advances episodes only with
  tenant-scoped, idempotent, evidence-bound lifecycle commands.

  Scenario: Snapshot validated source bytes with their exact hash and profile
    Given a tenant-scoped episode request with valid Markdown and profile
    When the episode is created
    Then the episode records the exact source SHA-256 and resolved profile

  Scenario: Reject an unknown source profile with a redacted structured failure
    Given a tenant-scoped episode request with an unknown profile
    When the episode is created
    Then creation returns a redacted validation failure with status 422

  Scenario: Replay the same tenant request idempotently
    Given a tenant-scoped episode request with valid Markdown and profile
    When the same episode request is created twice
    Then both creates return the same immutable episode at version 1

  Scenario: Reject conflicting reuse of a tenant idempotency key
    Given a created tenant-scoped episode
    When the tenant reuses its idempotency key with different source bytes
    Then creation returns a redacted idempotency conflict with status 409

  Scenario: Hide an episode from another tenant
    Given a created tenant-scoped episode
    When another tenant reads the episode
    Then the read returns a redacted not-found failure with status 404

  Scenario: Reject an illegal lifecycle transition
    Given a created tenant-scoped episode
    When the episode is published from its initial state
    Then transition returns a redacted invalid-transition failure with status 409

  Scenario: Require passing QA and an immutable package checksum
    Given an episode advanced through rendering
    When QA passes without evidence and packaging is attempted without a checksum
    Then both lifecycle gates return redacted invalid-transition failures with status 409

  Scenario: Require explicit authorization before publishing
    Given a packaged tenant-scoped episode
    When publishing is requested without authorization and then with authorization
    Then unauthorized publish returns a redacted authorization failure with status 403
    And authorized publish preserves the immutable package checksum

  Scenario: Reject a stale optimistic lifecycle update
    Given a created tenant-scoped episode
    When two transitions use the same expected version
    Then the stale transition returns a redacted version-conflict failure with status 409
