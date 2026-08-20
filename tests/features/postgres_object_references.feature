Feature: PostgreSQL object references and safe garbage collection
  Durable references protect tenant-scoped content-addressed objects.

  Scenario: An object reference replays exactly
    Given a recording PostgreSQL object-reference repository
    When I record and replay a PostgreSQL object reference
    Then the object reference is identical and tenant scoped

  Scenario: An object reference is removed only once
    Given a recording PostgreSQL object-reference repository
    When I record and remove a PostgreSQL object reference
    Then the PostgreSQL object reference is absent

  Scenario: Malformed PostgreSQL object metadata fails closed
    Given a recording PostgreSQL object-reference repository
    When the persisted object metadata is malformed
    Then the PostgreSQL object reference is rejected

  Scenario: Orphan collection retains referenced and young objects
    Given a conservative orphan collector
    When I collect stale PostgreSQL object orphans
    Then only the old unreferenced object is deleted

  Scenario: Inventory observations preserve first-seen age
    Given a recording PostgreSQL object-inventory repository
    When I observe the same object on two inventory passes
    Then the original first-seen time is retained

  Scenario: Scoped orphan cleanup joins inventory and references
    Given recording PostgreSQL reference and inventory repositories
    When I run scoped orphan collection
    Then only the unreferenced stale inventory key is deleted
