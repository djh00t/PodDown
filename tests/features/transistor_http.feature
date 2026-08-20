Feature: guarded Transistor publication transport
  External publication must remain explicit and auditable.

  Scenario: a Transistor create uses the authorized upload sequence
    Given a QA-passed package and a recording Transistor transport
    When I publish through the guarded Transistor adapter
    Then the adapter performs authorization upload and create in order
    And the provider credential is sent only as a request header

  Scenario: a transport failure after create is uncertain
    Given a QA-passed package and an uncertain Transistor transport
    When I publish through the guarded Transistor adapter
    Then the publication outcome is marked uncertain
