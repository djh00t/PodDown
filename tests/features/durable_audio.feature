Feature: Durable single-segment rendering

  Scenario: Render three deterministic takes for a rights-cleared segment
    Given a rights-cleared render request
    When the request is rendered with three local takes
    Then three immutable candidates and artifacts are returned
    And every candidate preserves the expected-spoken text
    And every candidate records zero-cost local usage

  Scenario: Refuse a request without valid voice consent before dispatch
    Given a render request without voice consent
    When the request is rendered with one local take
    Then rendering is rejected before the renderer is called
    And no artifact or cost event is recorded

  Scenario: Refuse a provider capability mismatch before dispatch
    Given a rights-cleared render request
    And the renderer cannot pin the requested voice
    When the request is rendered with one local take
    Then rendering is rejected before the renderer is called
    And no artifact or cost event is recorded

  Scenario: Replay returns the immutable candidate without duplicate cost
    Given a rights-cleared render request
    When the same request is rendered twice with one local take
    Then the second result is marked as replayed
    And the renderer is called only once
    And exactly one cost event exists

  Scenario: Concurrent services claim one request before provider dispatch
    Given a rights-cleared render request
    And a second service shares the render records
    When both services render the same local take concurrently
    Then the renderer is called only once
    And exactly one concurrent result is replayed

  Scenario: A new take has a distinct immutable identity
    Given a rights-cleared render request
    When the request is rendered with two local takes
    Then the two candidates have different candidate identities
    And the two artifacts have different content digests
