Feature: Explicit runtime composition

  Scenario: the API runtime refuses an implicit in-memory database
    Given an API environment without a SQLite database path
    When API runtime settings are loaded
    Then runtime configuration fails for "PODDOWN_SQLITE_PATH"

  Scenario: the API runtime selects durable local ports
    Given an API environment with a SQLite database path and Temporal settings
    When API runtime settings are loaded
    Then the API runtime uses the configured durable database and task queue

  Scenario: the API runtime accepts an explicit PostgreSQL data plane
    Given an API environment with a PostgreSQL DSN and Temporal settings
    When API runtime settings are loaded
    Then the API runtime selects PostgreSQL without a SQLite path

  Scenario: the worker persists publication receipts in PostgreSQL
    Given a worker configured for PostgreSQL publication receipts
    When worker publication composition is built
    Then the publication service uses the durable receipt store

  Scenario: the Temporal transport starts the immutable episode workflow
    Given a recording Temporal client factory
    When the Temporal transport starts a command request
    Then the Temporal client receives the immutable workflow payload

  Scenario: the Compose image declares host-local media prerequisites
    Given the Compose image runtime contract
    When the container media prerequisites are inspected
    Then the image installs FFmpeg and a Linux speech engine
