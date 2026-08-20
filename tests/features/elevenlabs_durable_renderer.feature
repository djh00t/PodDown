Feature: ElevenLabs durable renderer adapter
  Provider audio is normalized before it enters durable PodDown contracts.

  Scenario: Normalize an authorized ElevenLabs synthesis response
    Given an injected ElevenLabs synthesis client
    When I render one ElevenLabs request through the durable adapter
    Then the renderer preserves the request text and provider metadata

  Scenario: Use the durable candidate ledger without provider idempotency
    Given an injected ElevenLabs synthesis client
    When I preflight one ElevenLabs request through the durable service
    Then the durable candidate boundary accepts the request
