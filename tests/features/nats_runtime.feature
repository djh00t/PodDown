Feature: NATS JetStream runtime transport
  The runtime adapter connects to JetStream without becoming workflow authority.

  Scenario: Connect to and publish through JetStream
    Given a fake NATS connection
    When I publish a message through the NATS runtime transport
    Then the NATS subject and acknowledgement are preserved
