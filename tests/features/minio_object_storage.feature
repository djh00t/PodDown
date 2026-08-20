Feature: MinIO-compatible object storage integration
  The local MinIO target preserves PodDown's immutable, tenant-scoped object
  contract when the integration gate is explicitly enabled.

  Scenario: Replay an object through local MinIO
    Given a local MinIO object store
    When I store and replay a tenant-scoped Markdown object
    Then the local MinIO bytes are exact and replay is idempotent
