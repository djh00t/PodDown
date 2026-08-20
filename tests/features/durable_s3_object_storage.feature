Feature: Durable S3 object storage
  Blob bytes and tenant-scoped object references form one safe storage boundary.

  Scenario: A durable object put records and replays its reference
    Given a fake blob store and durable object-reference repository
    When I put and read a durable object twice
    Then the durable object and reference replay identically

  Scenario: A read without a durable reference fails closed
    Given a fake blob store and durable object-reference repository
    When I read an unrecorded durable object
    Then the durable object read is rejected
