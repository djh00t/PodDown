Feature: Safe PodDown agent integrations
  Agents use a tenant-bound, approval-aware local MCP boundary.

  Scenario: Preview is selected before render for uncertain source
    Given an authenticated tenant MCP server
    When the agent calls poddown_preview with Markdown source
    Then the preview result preserves the exact source digest
    And no paid side effect is recorded

  Scenario: Tenant scope cannot be supplied by a tool caller
    Given an authenticated tenant MCP server
    When the agent supplies a different tenant ID to poddown_get_episode
    Then the tool returns a stable invalid-input error
    And the gateway receives only the authenticated tenant

  Scenario: Publish requires fresh scoped approval
    Given an authenticated tenant MCP server
    When the agent calls poddown_publish without fresh approval
    Then the tool returns a stable approval-required error
    And no publish side effect is recorded

  Scenario: MCP failures are redacted
    Given an authenticated tenant MCP server with a sensitive gateway failure
    When the agent calls poddown_get_status
    Then the response contains no source, credential, voice ID, or provider payload

  Scenario: Audio is exposed as an authorized resource link
    Given an authenticated tenant MCP server with an authorized audio resource
    When the agent calls poddown_get_episode
    Then the response contains a resource link and no inline audio bytes
