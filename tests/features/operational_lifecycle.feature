Feature: operational retention and recovery evidence

  Scenario: expired mutable data is eligible for deletion
    Given a retention policy with independent source and audio windows
    When source data is evaluated after its retention window
    Then the source deletion decision is eligible

  Scenario: immutable publication evidence is protected
    Given expired publication evidence without a deletion authorization
    When the publication retention decision is evaluated
    Then deletion is blocked with an audit reason

  Scenario: a tenant export binds exact scoped bytes
    Given two scoped export artifacts for one tenant and project
    When an export manifest is captured
    Then the manifest verifies the original artifacts
    And a changed artifact fails manifest verification

  Scenario: a restore manifest rejects a cross-tenant artifact
    Given a backup manifest for one tenant and project
    When a restore is attempted with an artifact from another tenant
    Then restore verification fails closed

  Scenario: a filesystem archive round-trips exact scoped bytes
    Given two scoped export artifacts for one tenant and project
    When a filesystem export archive is written and restored
    Then the restored archive matches the exact manifest and artifacts
    And a tampered filesystem archive fails closed
