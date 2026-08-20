Feature: Repository-backed workflow status activity
  Workflow status reads use the authoritative repository with tenant and project scope.

  Scenario: Return the complete scoped status contract
    Given an authoritative status record for a tenant project and episode
    When the status activity reads that tenant project and episode
    Then it returns the complete authoritative status contract

  Scenario: Hide a record from another tenant
    Given an authoritative status record for a tenant project and episode
    When the status activity reads it as another tenant
    Then the status activity reports a safe not-found lookup error

  Scenario: Hide a record from another project
    Given an authoritative status record for a tenant project and episode
    When the status activity reads it from another project
    Then the status activity reports a safe not-found lookup error

  Scenario: Fail closed for a missing record
    Given no authoritative status record
    When the status activity reads a tenant project and episode
    Then the status activity reports a safe not-found lookup error

  Scenario: Project only safe failure fields
    Given an authoritative failed status record with internal failure detail
    When the status activity reads that tenant project and episode
    Then its failure projection contains only safe status fields
