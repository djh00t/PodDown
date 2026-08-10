Feature: Temporal episode render orchestration
  The local demo workflow selects durable candidates without contacting a provider.

  Scenario: Empty episode input is rejected before any audio dispatch
    Given an empty episode workflow input
    When I validate the workflow input
    Then the workflow input is rejected before audio activity

  Scenario: Fan out no more than three deterministic takes for every segment
    Given a deterministic two-segment episode with a three-take budget
    When the local episode workflow is run
    Then every segment has exactly three candidates in take order
    And no segment receives more than three takes in an attempt

  Scenario: A hard-gate failure cannot be rescued by a higher soft score
    Given a segment with a high-score candidate that fails a hard gate
    And a lower-score candidate that passes every hard gate
    When the local episode workflow is run
    Then the hard-gate-passing candidate is accepted
    And the hard-gate-failing candidate is not accepted

  Scenario: A transient render retry records one accepted cost event
    Given a segment whose first render activity fails transiently
    When the local episode workflow is run
    Then the segment succeeds on its second attempt
    And exactly one accepted cost event is recorded for the segment

  Scenario: Repair rerenders only failed segments
    Given a two-segment episode with one accepted segment and one failed segment
    When the local episode workflow is run
    Then the accepted segment is not dispatched for repair
    And the failed segment is dispatched for its second attempt only

  Scenario: A completed workflow is replayed without new dispatch or cost
    Given a completed deterministic episode workflow
    When the same immutable episode input is run again
    Then the existing completed workflow result is returned
    And replay records no additional dispatches or accepted cost events
