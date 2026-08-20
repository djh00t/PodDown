Feature: clean-checkout reference verifier
  The verifier validates local demo evidence without live services.

  Scenario: a valid deterministic demo result is accepted
    Given a valid deterministic demo result
    When the demo result is verified
    Then the clean-checkout demo evidence is accepted

  Scenario: a non-live result cannot claim provider evidence
    Given a deterministic demo result with provider ASR evidence
    When the demo result is verified
    Then the clean-checkout demo evidence is rejected

  Scenario: a valid host-local demo result is accepted with media evidence
    Given a valid host-local demo result and published media evidence
    When the host-local demo result is verified
    Then the clean-checkout listening evidence is accepted

  Scenario: host-local verification rejects provider claims
    Given a host-local demo result with provider ASR evidence
    When the host-local demo result is verified
    Then the clean-checkout listening evidence is rejected
