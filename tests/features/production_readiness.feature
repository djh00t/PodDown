Feature: Offline production-readiness contracts
  The local readiness slice is deterministic and never requires live infrastructure.

  Scenario: Report liveness and dependency degradation separately
    Given an offline production-readiness evaluator
    When PostgreSQL is unavailable but the process is alive
    Then liveness is healthy and readiness is degraded

  Scenario: Redact unsafe operational fields
    Given an offline operational event with source audio and credential fields
    When the event is serialized
    Then the event contains tenant-safe metadata and no unsafe values

  Scenario: Validate the local Compose topology without Docker
    Given the versioned local Compose contract
    When the Compose YAML is parsed
    Then it contains the six required runtime services and health-gated dependencies
