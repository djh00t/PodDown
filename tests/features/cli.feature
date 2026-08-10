Feature: PodDown CLI and GitHub automation
  The CLI is a thin deterministic client of the public contracts.

  Scenario: Preview validates Markdown without provider calls
    Given a valid Markdown source file
    When I preview the source as JSON
    Then preview succeeds with resolved profile and no HTTP calls

  Scenario: Preview honors flag configuration precedence
    Given a source with frontmatter and project configuration
    When I preview with an explicit profile flag
    Then the flag profile wins in stable JSON output

  Scenario: Preview accepts an explicit endpoint without HTTP calls
    Given a valid Markdown source file
    When I preview with an explicit endpoint as JSON
    Then preview reports the endpoint without HTTP calls

  Scenario: Render submits an idempotent asynchronous command
    Given a valid Markdown source file
    When I render the source through the fake API as JSON
    Then render returns a queued receipt after two API calls

  Scenario: Status reports a durable remote state
    When I query status through the fake API as JSON
    Then status returns a packaged state

  Scenario: Publish sends explicit authorization
    When I publish with confirmation through the fake API as JSON
    Then publish returns success with publish authorization

  Scenario: Publish requires explicit confirmation
    When I publish without confirmation
    Then publish fails with an authorization exit and no HTTP calls

  Scenario: Package checksum mismatch is rejected
    Given a local episode package
    When I install the package with a wrong checksum
    Then installation fails without writing the destination

  Scenario: Untrusted pull request validation is provider-free
    Given the pull request validation action definition
    Then it keeps pull-request preview separate from protected operations
