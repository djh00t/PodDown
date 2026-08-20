Feature: Verified authentication and tenant scope
  Production requests use verified OIDC claims; header compatibility is local-only.

  Scenario: A verified OIDC token creates an authenticated principal
    Given an API authentication verifier with a generated signing key
    When I verify a token for a tenant and project
    Then the principal contains the verified tenant, project and scopes

  Scenario: A token with an invalid signature fails closed
    Given an API authentication verifier with a generated signing key
    When I verify a token signed by another generated key
    Then authentication is rejected without a principal

  Scenario: Local headers require explicit local mode
    Given a local authentication verifier
    When I resolve a tenant and project from compatibility headers
    Then the local principal is scoped to those identifiers
