Feature: provider transport resilience
  Provider HTTP dispatch stays bounded and observable without live credentials.

  Scenario: rate limits expose a server retry delay
    Given a provider rate-limit response with Retry-After 3 seconds
    When the provider response is classified
    Then the rate-limit error exposes a retry delay of 3 seconds

  Scenario: a transient provider failure retries within its dispatch bound
    Given a provider transport that times out before succeeding
    When the resilient provider transport dispatches the request
    Then the request succeeds after 2 attempts and one 1-second delay

  Scenario: an open provider circuit fails closed until cooldown
    Given a provider circuit opened by consecutive transient failures
    When another provider request is dispatched before cooldown
    Then the circuit rejects the request without provider dispatch
