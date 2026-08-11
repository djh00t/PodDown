Feature: Label execution evidence honestly
  Deterministic and host-local runs remain useful but cannot claim live eligibility.

  Scenario Outline: Local evidence is accepted only when labelled as non-live
    Given a "<mode>" execution record with "<render_evidence>" render evidence
    When execution evidence is validated
    Then the record is accepted as non-live evidence

    Examples:
      | mode                | render_evidence |
      | deterministic-local | synthetic-bytes |
      | host-local          | host-tts        |

  Scenario Outline: Local evidence cannot claim live eligibility
    Given a "<mode>" execution record with "<render_evidence>" render evidence
    And the execution record is marked live eligible
    When execution evidence is validated
    Then the record is rejected as false live evidence

    Examples:
      | mode                | render_evidence |
      | deterministic-local | synthetic-bytes |
      | host-local          | host-tts        |
