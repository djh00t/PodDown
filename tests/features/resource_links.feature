Feature: Authorized resource links
  Episode resources are short-lived, signed and scope-bound.

  Scenario: A signed resource link verifies for its original scope
    Given a resource-link signer
    When I issue and verify an audio resource link
    Then the link verifies for the same tenant and episode

  Scenario: A link cannot cross tenant scope
    Given a resource-link signer
    When I verify the link for another tenant
    Then the resource link is rejected

  Scenario: An expired link fails closed
    Given a resource-link signer
    When I verify an expired resource link
    Then the resource link is rejected
