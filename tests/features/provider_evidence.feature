Feature: Frozen provider evidence
  Provider outcomes retain normalized, auditable metadata without retaining raw inputs.

  Scenario: A live transcription outcome records only normalized provider evidence
    Given complete live transcription provider evidence
    When the provider evidence is frozen
    Then the normalized provider evidence is retained without raw payload fields
