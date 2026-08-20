Feature: Authenticated API agent gateway
  The API MCP gateway delegates through the authenticated HTTP API.

  Scenario: API preview does not forward model-supplied tenant scope
    Given an API agent gateway transport
    When the gateway executes an API preview
    Then the request uses bearer authentication without tenant headers
