Feature: Tenant-scoped transactional outbox
  Authoritative state changes and integration events share one database transaction.

  Scenario: An identical event replays without duplication
    Given a recording PostgreSQL outbox connection
    When I append the same outbox event twice
    Then the event is inserted once and both calls return the same event

  Scenario: A conflicting event identity fails closed
    Given a recording PostgreSQL outbox connection
    When I append an event identity with different payload
    Then the outbox conflict is rejected

  Scenario: Published events are acknowledged only once
    Given a recording PostgreSQL outbox connection
    When I mark an outbox event published twice
    Then the event has one immutable publication timestamp

  Scenario: A published event replays without losing its publication state
    Given a recording PostgreSQL outbox connection
    When I append an already published event
    Then the replay retains the publication timestamp
