Feature: Source-bound adaptation envelopes
  Provider adaptation responses must remain schema-preserving and auditable.

  Scenario: Parse a source-bound adaptation envelope
    Given a valid source-bound adaptation envelope
    When I parse the adaptation envelope
    Then the envelope preserves its source hash and anchors

  Scenario: Reject an adaptation envelope with an unsupported field
    Given a valid source-bound adaptation envelope with an extra field
    When I parse the adaptation envelope
    Then the adaptation envelope is rejected

