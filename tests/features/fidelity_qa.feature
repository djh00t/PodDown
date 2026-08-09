Feature: Verify factual fidelity of rendered audio
  Critical factual tokens must be correct in every accepted candidate.

  Scenario Outline: Reject a mistranscribed critical token
    Given a canonical segment containing the critical token "<expected>"
    And its candidate transcript contains "<actual>"
    When candidate fidelity is evaluated
    Then the candidate fails the critical-token gate
    And only its segment is eligible for rerendering

    Examples:
      | expected                | actual                 |
      | one point six terabit   | one point six trillion |
      | twenty-one kilograms   | twenty-one kilometres |
      | is not supported       | is supported           |

  Scenario: Accept a candidate with exact critical-token fidelity
    Given a canonical segment containing the critical token "see one"
    And its candidate transcript contains "see one"
    When candidate fidelity is evaluated
    Then critical-token accuracy is 1.0
    And the candidate may proceed to audio quality evaluation
