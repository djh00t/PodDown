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

  Scenario: Complete provider evidence earns live eligibility
    Given a live-provider execution record with provider evidence and complete metadata
    When execution evidence is validated
    Then the record is accepted as live evidence

  Scenario: Legacy execution_mode wire key is rejected
    Given an execution record with legacy execution_mode key
    When execution evidence is validated
    Then the record is rejected as malformed execution evidence

  Scenario: Legacy singular provider request_id fields are rejected
    Given a live-provider execution record with provider evidence and complete metadata
    And provider evidence uses singular request_id fields
    When execution evidence is validated
    Then the record is rejected as malformed execution evidence
