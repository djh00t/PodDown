Feature: Local NATS JetStream outbox delivery
  The local JetStream boundary preserves tenant-scoped subjects and replay identity.

  Scenario: Duplicate outbox publishes are deduplicated by JetStream
    Given an opt-in local NATS JetStream outbox
    When I publish the same outbox event twice
    Then the local JetStream stores one message with the stable event identity
