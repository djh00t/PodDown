Feature: Verify final-master transcription and fidelity
  A verified master must pass independent fidelity QA before it is package-ready.

  Scenario: Accept an independently transcribed final master with exact fidelity
    Given a valid final master and a matching transcription of "C1 arrives at noon"
    And its first critical token is "C1"
    And its second critical token is "noon"
    When final-master QA is evaluated
    Then the final-master QA record passes at stage "master"
    And final-master critical-token accuracy is 1.0
    And final-master checksum evidence is bound to the exact master bytes
    And the transcriber receives the exact master once

  Scenario: Reject a final master whose transcript omits a critical token
    Given a valid final master and a matching transcription of "C1 arrives"
    And its first critical token is "C1"
    And its second critical token is "noon"
    When final-master QA is evaluated
    Then the final-master QA record fails at stage "master"
    And final-master critical-token accuracy is 0.5

  Scenario: Fail closed when transcript evidence belongs to different audio
    Given a valid final master and a transcription with a different checksum
    And its critical tokens are "C1"
    When final-master QA is evaluated
    Then final-master QA raises a terminal transcription failure

  Scenario: Reject malformed final-master media before transcription dispatch
    Given a malformed final master
    And its critical tokens are "C1"
    When final-master QA is evaluated
    Then final-master QA rejects the master before transcription dispatch

  Scenario Outline: Map provider failures to stable final-master QA errors
    Given a valid final master and a transcriber that raises <provider failure>
    And its critical tokens are "C1"
    When final-master QA is evaluated
    Then final-master QA raises a <mapped failure> failure

    Examples:
      | provider failure | mapped failure |
      | timeout          | retryable      |
      | rate limit       | retryable      |
      | terminal error   | terminal       |

  Scenario: Serialize equivalent final-master QA evidence deterministically
    Given two equivalent final masters with matching transcription of "C1 arrives at noon"
    And its first critical token is "C1"
    And its second critical token is "noon"
    When both final-master QA evaluations are performed
    Then their serialized final-master QA records are identical
    And the serialized record contains only QA schema fields
