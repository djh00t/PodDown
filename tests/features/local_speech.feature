Feature: Host-local speech rendering

  Scenario: An available local engine produces canonical speech WAV
    Given an available host-local speech engine
    When I render a local speech request
    Then I receive canonical zero-cost speech WAV audio

  Scenario: A missing local executable fails closed
    Given the requested local speech executable is unavailable
    When I render a local speech request
    Then local speech rendering fails closed

  Scenario: Equivalent local speech takes reuse normalized audio
    Given an available host-local speech engine
    When I render three takes with the same text and voice
    Then the local engine renders once and each take has a unique request ID
