Feature: Derive episode API scope from a verified principal
  Production episode routes accept scope only from an injected verified principal.

  Scenario: Reject forged tenant and project headers in production
    Given a production episode API client with an injected verified principal
    When I submit an episode with forged tenant and project headers
    Then the production scope response is forbidden

  Scenario: Reject a production request without an authenticated principal
    Given a production episode API client without an injected principal
    When I submit an episode with tenant and project headers
    Then the production scope response requires an authenticated principal

  Scenario: Accept an allowlisted project in production
    Given a production episode API client with an injected verified principal
    When I submit an episode for an allowlisted project
    Then the production scope response is accepted for the verified tenant

  Scenario: Retain headers only in explicit local mode
    Given a local episode API client without an injected principal
    When I submit an episode with tenant and project headers
    Then the production scope response is accepted for the verified tenant
