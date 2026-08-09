Feature: Render segments through rights-cleared provider adapters
  Providers render immutable segments without owning PodDown content decisions.

  Scenario Outline: Render through an eligible provider
    Given a canonical rights-cleared segment
    And the "<provider>" renderer is eligible
    When one candidate is requested from "<provider>"
    Then the provider receives the immutable expected-spoken text
    And the candidate records provider request, model, usage, cost, and checksum

    Examples:
      | provider   |
      | elevenlabs |
      | openai     |

  Scenario: Prevent rendering with revoked voice consent
    Given a canonical segment whose voice consent is revoked
    When one candidate is requested from "elevenlabs"
    Then rendering fails before any provider call

  Scenario: Fall back without weakening quality gates
    Given a canonical rights-cleared segment
    And the "elevenlabs" renderer is transiently unavailable
    And the "openai" renderer is eligible
    When rendering falls back to "openai"
    Then a distinct provider candidate is recorded
    And all critical-token and audio gates remain required
