Feature: Preserve production-closure evidence truth
  Script-derived local evidence remains useful without becoming live-provider proof.

  Scenario Outline: Script-derived local evidence is accepted only as non-live
    Given a C07 "<mode>" record with "<render_evidence>" render evidence
    When C07 execution evidence is validated
    Then the C07 record is accepted as non-live evidence

    Examples:
      | mode                | render_evidence |
      | deterministic-local | synthetic-bytes |
      | host-local          | host-tts        |

  Scenario Outline: Script-derived local evidence cannot claim live-provider eligibility
    Given a C07 "<mode>" record with "<render_evidence>" render evidence
    And the C07 record is marked live eligible
    When C07 execution evidence is validated
    Then the C07 record is rejected as false live evidence

    Examples:
      | mode                | render_evidence |
      | deterministic-local | synthetic-bytes |
      | host-local          | host-tts        |

  Scenario Outline: Every live-provider gate is required
    Given a complete C07 live-shaped evidence record
    And the C07 "<gate>" live gate is invalid
    When C07 execution evidence is validated
    Then the C07 record is rejected as false live evidence

    Examples:
      | gate                    |
      | live-provider mode      |
      | provider render evidence |
      | provider ASR evidence   |
      | valid consent           |
      | renderer metadata       |
      | transcriber metadata    |
      | cost evidence           |

  Scenario Outline: Live eligibility requires exact full numeric token accuracy
    Given a complete C07 live-shaped evidence record
    And C07 critical-token accuracy is "<accuracy>"
    When C07 execution evidence is validated
    Then the C07 record is rejected as false live evidence

    Examples:
      | accuracy |
      | 0.999    |
      | true     |

  Scenario: Complete live-shaped evidence with numeric 1.0 is live eligible
    Given a complete C07 live-shaped evidence record
    When C07 execution evidence is validated
    Then the C07 record is accepted as live evidence
