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

  Scenario: Publish approval is trusted, bounded, and one-time
    Given an authenticated tenant MCP server with a trusted approval registry
    When the agent presents a valid approval and replays it
    Then the first publish succeeds and the replay is rejected

  Scenario: Missing or stale approval fails closed
    Given an authenticated tenant MCP server with a trusted approval registry
    When the agent presents a missing or stale approval
    Then publish is rejected without a side effect

  Scenario: MCP negotiates before exposing tools
    Given an authenticated tenant MCP server
    When the MCP client initializes and lists tools
    Then the handshake returns the supported protocol and capabilities
    And tools are returned as MCP tool objects

  Scenario: MCP tool calls use the CallToolResult envelope
    Given an authenticated tenant MCP server
    When the MCP client calls preview through stdio
    Then the response contains content and structured content

  Scenario: Malformed tool arguments do not kill stdio
    Given an authenticated tenant MCP server
    When the MCP client sends null tool arguments followed by a valid call
    Then stdio returns an invalid-parameters result and continues

  Scenario: Malformed JSON-RPC framing does not kill stdio
    Given an authenticated tenant MCP server
    When the MCP client sends malformed JSON-RPC framing followed by initialize
    Then stdio returns a framing error and continues
