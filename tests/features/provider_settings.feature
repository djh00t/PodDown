Feature: Provider runtime settings
  Provider controls remain explicit and secret-safe before any live dispatch.

  Scenario: A configured live provider route has no credential value in its record
    Given an explicitly enabled live provider route
    When provider runtime settings are parsed
    Then the settings retain only secret references

  Scenario: A live provider route without opt-in is rejected before dispatch
    Given a live provider route without opt-in
    When provider runtime settings are parsed
    Then the provider settings are rejected
