Feature: S3-compatible immutable object storage
  MinIO and other S3-compatible stores use tenant-scoped content-addressed objects.

  Scenario: An exact object put replays safely
    Given a recording S3-compatible object transport
    When I put and read the same object twice
    Then the object reference and bytes are identical

  Scenario: A tampered object is rejected
    Given a recording S3-compatible object transport
    When the stored bytes are tampered before reading
    Then object integrity fails closed

  Scenario: Cross-tenant reads are rejected
    Given a recording S3-compatible object transport
    When another tenant reads the object
    Then object scope fails closed

  Scenario: The S3 object store inventories one project prefix
    Given a recording S3-compatible object transport
    When I inventory the tenant project objects
    Then only canonical project keys are returned

  Scenario: Inventory deletion revalidates an unreferenced object
    Given a recording S3-compatible object transport
    When I delete one unreferenced inventory key
    Then the verified object is removed

  Scenario: S3 maintenance protects referenced objects
    Given a recording S3-compatible object transport
    When I run reference-aware S3 orphan cleanup
    Then only the stale unreferenced S3 object is removed
