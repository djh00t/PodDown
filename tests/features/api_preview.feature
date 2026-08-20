Feature: Preview source through the authenticated Episode API
  Preview is deterministic, provider-free, and side-effect free.

  Scenario: Preview a valid source without provider work
    Given an offline episode API client for preview
    When I submit valid Markdown to the preview route
    Then the preview response preserves the source digest and reports no side effect

  Scenario: Reject an unknown preview profile safely
    Given an offline episode API client for preview
    When I submit an unknown profile to the preview route
    Then the preview response is a stable invalid-profile problem
