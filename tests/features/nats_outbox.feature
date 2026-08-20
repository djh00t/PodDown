Feature: NATS JetStream outbox delivery
  Transactional outbox events are delivered with replay-safe message identity.

  Scenario: An outbox event is published with a stable deduplication identity
    Given a recording NATS JetStream client and outbox connection
    When I relay one NATS outbox event
    Then the event is acknowledged with its message identity

  Scenario: A failed publish records an attempt and remains pending
    Given a failing NATS JetStream client and outbox connection
    When I relay one NATS outbox event
    Then the failed NATS event remains pending with one attempt

  Scenario: An opt-in relay activity publishes one tenant-scoped batch
    Given a configured NATS outbox relay activity
    When the NATS outbox relay activity runs for one tenant
    Then the relay activity returns the published event identity

  Scenario: A tenant relay workflow invokes the bounded relay activity
    Given a tenant-scoped outbox relay workflow input
    When the tenant relay workflow runs
    Then the workflow returns the relay report with bounded retry policy
