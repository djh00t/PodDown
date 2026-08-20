Feature: Transactional outbox
  Durable domain mutations can enqueue events without publishing them.

  Scenario: A rolled-back episode mutation leaves no outbox event
    Given an open transaction with an episode mutation
    When the mutation enqueues its episode event and the transaction rolls back
    Then neither the episode mutation nor the outbox event is durable

  Scenario: An event is safely replayed in one caller transaction
    Given an open transaction with a deterministic episode event
    When the same event is enqueued twice
    Then one deterministic outbox event is retained without a commit

  Scenario: A released event waits for its explicit retry delay
    Given a committed deterministic episode event
    When a worker claims the event and releases it for retry
    Then the event remains unavailable until the explicit retry delay ends

  Scenario: An expired worker cannot postpone an event
    Given a committed deterministic episode event
    And a worker claim whose lease has expired
    When the expired worker releases the event for retry
    Then the event retains its expired worker lease
