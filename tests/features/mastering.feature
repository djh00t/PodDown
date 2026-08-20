Feature: Deterministic episode mastering

  Scenario: Master accepted segments in stable position order with provenance
    Given two valid segments supplied out of position order
    When the episode is mastered
    Then deterministic WAV and MP3 outputs are returned
    And the runner receives segments in stable position order
    And provenance records the profile, runner, ordered inputs, and output checksums

  Scenario: Reject malformed audio before runner dispatch
    Given a mastering request containing malformed audio
    When the episode is mastered
    Then mastering fails before the runner is called

  Scenario: Surface runner failures as mastering errors
    Given a valid mastering request and a failing runner
    When the episode is mastered
    Then the runner failure is raised as a mastering error

  Scenario: Reject output without MP3 inspection evidence
    Given a valid mastering request and a runner without MP3 metadata
    When the episode is mastered
    Then mastering fails closed for missing MP3 inspection

  Scenario: Equivalent inputs retain output checksums
    Given two equivalent mastering requests
    When both episodes are mastered
    Then their WAV and MP3 output checksums are equal

  Scenario: Accept short segments for a longer episode master
    Given a short segment and a longer master duration requirement
    When the episode is mastered
    Then the short segment is dispatched and the longer master is returned

  Scenario: Scale the FFmpeg timeout for a long episode
    Given short and long assembled WAV durations
    When the FFmpeg timeouts are calculated
    Then the long episode timeout is greater than the short episode timeout
