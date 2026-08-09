Feature: Prepare source-bound technical content

  Scenario: Prepare a difficult two-speaker technical source
    Given the robotics mapping source and technical dialogue profile
    And a deterministic source-bound adaptation proposal
    When content intelligence prepares the episode
    Then the canonical script has two stable speakers and complete factual anchors
    And the script contains disagreement without unsupported claims
    And every extracted critical token has an expected spoken form
    And the segmentation manifest preserves turn order and source grouping

  Scenario: Reject a changed number before rendering
    Given the robotics mapping source and a proposal that changes a source number
    When content intelligence prepares the episode
    Then adaptation fails with an unsupported claim error
    And no renderer call is made

  Scenario: Resolve pronunciation layers deterministically
    Given four lexicon layers for the key "C1"
    When the pronunciation is resolved
    Then the episode layer wins and its version and entry ID are recorded

  Scenario: Reject a same-priority pronunciation conflict
    Given two project lexicon entries for the normalized key "LiDAR"
    When the project pronunciation is resolved
    Then lexicon resolution fails closed with a conflict error

  Scenario: Preserve repeated negation and critical-token occurrences
    Given the text "The system is not silent, and it is not stable"
    When critical tokens are extracted
    Then two distinct negation occurrences are present
    And the token manifest is deterministic

  Scenario: Reject a provider capability that would split a complete turn
    Given a canonical script with a turn longer than the renderer text limit
    When the script is segmented
    Then segmentation fails with a capability error
