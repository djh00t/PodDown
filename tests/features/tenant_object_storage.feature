Feature: Tenant-scoped immutable object storage
  The local adapter stores exact bytes under tenant/project content-addressed
  keys without requiring cloud services or credentials.

  Scenario: Store and replay an immutable object
    Given an empty tenant object store
    When I put the same Markdown bytes twice
    Then both references and reads are identical

  Scenario: Keep object keys isolated by tenant and project
    Given an empty tenant object store
    When I put identical bytes for two project scopes
    Then the canonical keys are distinct and cross-scope reads fail

  Scenario: Reject corrupted immutable bytes
    Given an object stored in the tenant object store
    When the stored bytes are corrupted
    Then the object read fails with an integrity error
