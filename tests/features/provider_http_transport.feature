Feature: Provider HTTP transport
  Provider adapters can use a concrete async HTTP boundary without owning sockets.

  Scenario: JSON provider requests preserve method headers and body
    Given a recording urllib provider opener
    When I send a JSON provider request
    Then the opener receives the exact JSON request

  Scenario: Multipart provider requests preserve form and audio parts
    Given a recording urllib provider opener
    When I send a multipart provider request
    Then the opener receives the form and audio parts
