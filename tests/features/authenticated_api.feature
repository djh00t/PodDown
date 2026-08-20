Feature: Authenticated API scope
  API mode derives tenant and project scope from the verified principal.

  Scenario: API creation uses principal scope
    Given an API app with a verified principal
    When I create an episode with a bearer authorization
    Then the episode scope comes from the principal

  Scenario: API mode rejects compatibility scope headers
    Given an API app with a verified principal
    When I create an episode with tenant and project headers
    Then the API rejects compatibility scope headers

  Scenario: API publish requires a scoped approval body
    Given an API app with a verified principal
    When I request publication with only the compatibility authorization header
    Then the API rejects the legacy publish authorization
