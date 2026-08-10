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

  Scenario: Reject an ancestor symlink traversal
    Given an object stored in the tenant object store
    When an object-store ancestor is replaced by a symlink
    Then the object read fails with an integrity error

  Scenario: Bind reads to persisted reference metadata
    Given an object stored in the tenant object store
    When I read it with changed display metadata
    Then the object read fails with an integrity error

  Scenario: Reject a missing immutable object
    Given an object stored in the tenant object store
    When the stored object is removed
    Then the object read fails with a missing-object error

  Scenario: Recover an interrupted object metadata publication
    Given an object stored in the tenant object store
    When I replay its put after removing the metadata sidecar
    Then the replay recovers the exact metadata sidecar

  Scenario: Reject malformed object input
    Given an empty tenant object store
    When I submit a UUID4 scope and path-traversal name
    Then both puts fail with validation errors

  Scenario: Reject a malformed reference checksum
    Given an empty tenant object store
    When I construct a malformed checksum reference
    Then reference construction fails validation

  Scenario: Keep local object storage provider-free
    Given an empty tenant object store
    Then the object store is local and provider-free
