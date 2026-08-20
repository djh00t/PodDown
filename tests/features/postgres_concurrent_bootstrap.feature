Feature: Concurrent PostgreSQL runtime bootstrap
  The API and worker may initialize the same durable database at the same time.

  Scenario: Concurrent runtime bootstrap is serialized
    Given an isolated PostgreSQL schema for concurrent bootstrap
    When two runtime components initialize PostgreSQL concurrently
    Then both bootstrap calls complete with the full migration catalog
