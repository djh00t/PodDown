Feature: Submit and monitor tenant-scoped episodes over HTTP
  The asynchronous episode API accepts only explicit tenant context and never
  requires a provider, network service, or storage adapter for this contract.

  Scenario: Accept a valid episode creation request
    Given an offline episode API client with valid tenant headers
    When I submit valid Markdown for the registered profile
    Then the create response is accepted with a UUIDv7 summary and command receipt

  Scenario: Replay an identical create request
    Given an offline episode API client with valid tenant headers
    When I submit the same create request twice
    Then both create responses describe the same episode and receipt

  Scenario: Reject conflicting create idempotency reuse
    Given an offline episode API client with valid tenant headers
    When I reuse the create idempotency key with different Markdown
    Then the response is a stable redacted conflict problem

  Scenario: Reject invalid create context without leaking source
    Given an offline episode API client with valid tenant headers
    When I omit or invalidate tenant project idempotency profile or source encoding
    Then every response is a stable redacted client problem

  Scenario: Hide every episode route from another tenant
    Given an offline episode API client with a created episode
    When another tenant gets the episode status render and publish routes
    Then every cross-tenant response is a redacted not-found problem

  Scenario: Return redacted status details
    Given an offline episode API client with a created episode
    When I request the episode status
    Then the status response contains no source or credential material

  Scenario: Report the current lifecycle version in status
    Given an offline episode API client with a created episode
    When I request the episode status
    Then the status response contains the current episode version

  Scenario: Expose allowlisted failure details in status
    Given an offline episode API client with a failed episode
    When I request the failed episode status
    Then the failure response contains only allowlisted fields

  Scenario: Submit an idempotent non-blocking render command
    Given an offline episode API client with a created episode
    When I submit the same render request twice
    Then both render responses return the same queued command receipt

  Scenario: Require explicit authorization to publish
    Given an offline episode API client with a created episode
    When I publish without explicit authorization and then with authorization
    Then publish is forbidden without authorization and remains gated before packaging
