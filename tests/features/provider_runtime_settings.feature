Feature: Provider runtime settings
  Runtime provider controls are explicit, validated, and secret-safe.

  Scenario: Live provider settings require explicit dispatch controls
    Given a valid live provider route record
    When live provider runtime settings are loaded
    Then the route remains live-provider and explicitly enabled

  Scenario: Local provider settings remain offline
    Given a valid deterministic-local route record
    When local provider runtime settings are loaded
    Then the route remains offline with zero cost

  Scenario: Invalid live controls fail before dispatch
    Given a valid live provider route record
    When live provider runtime settings are loaded without opt-in
    Then provider dispatch is rejected before any request
