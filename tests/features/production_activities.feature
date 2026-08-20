Feature: Durable production stage activities
  Production workers must persist exact stage evidence and expose distinct package digests.

  Scenario: Replay persisted master and final-master QA evidence
    Given a durable production stage fixture
    When the persisted production stage evidence is replayed
    Then the master and final-master QA evidence is identical

  Scenario: Commit a package with separate byte and manifest digests
    Given a durable production package activity fixture
    When the durable package activity runs
    Then it returns separate package and manifest digests
