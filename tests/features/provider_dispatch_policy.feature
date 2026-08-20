Feature: Provider dispatch preflight
  Provider calls require explicit rights, registered metadata, and budget headroom.

  Scenario: A complete live render request is allowed before dispatch
    Given a complete registered live render request
    And valid consent for the configured voice and provider
    When provider dispatch preflight is evaluated
    Then dispatch is allowed without policy reasons

  Scenario: Revoked consent and exhausted budgets block live dispatch
    Given a complete registered live render request
    And revoked consent for the configured voice and provider
    And a request that exceeds both cost budgets
    When provider dispatch preflight is evaluated
    Then dispatch is denied with consent and budget reasons

  Scenario: A registered transcription request does not require voice consent
    Given a complete registered live transcription request
    When provider dispatch preflight is evaluated
    Then dispatch is allowed without policy reasons

  Scenario: Unknown routes fail closed
    Given a complete registered live render request
    And the request names an unknown route
    When provider dispatch preflight is evaluated
    Then dispatch is denied because the route is not registered

  Scenario: Local rendering remains credential-free and zero-cost
    Given a complete deterministic-local render request
    When provider dispatch preflight is evaluated
    Then dispatch is allowed without policy reasons
    And the resolved local route has zero cost and no secret metadata
