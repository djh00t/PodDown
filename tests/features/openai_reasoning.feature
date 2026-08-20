Feature: Dispatch a structured adaptation reasoning request
  The transport sends only the approved R02 request and returns safe normalized
  response metadata for later adaptation validation.

  Scenario: A strict Responses reply is normalized without credentials
    Given an approved reasoning request and a successful Responses reply
    When the OpenAI reasoning transport is called
    Then the response text and provider metadata are available for adaptation
    And the dispatched Responses request contains no credentials
