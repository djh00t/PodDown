Feature: Retrieve signed tenant-scoped resources through the Episode API
  Resource links are short-lived, authenticated, and checksum-bound.

  Scenario: Retrieve a signed resource without exposing storage details
    Given an offline episode API client with a signed resource
    When I request the signed resource URL
    Then the API returns the exact resource bytes and media type

  Scenario: Reject a tampered signed resource URL
    Given an offline episode API client with a signed resource
    When I tamper with the signed resource URL
    Then the API rejects the resource without returning bytes
