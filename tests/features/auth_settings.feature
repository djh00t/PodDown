Feature: Authentication settings
  Authentication defaults to OIDC so production requests cannot gain authority from headers.

  Scenario: Unconfigured authentication is OIDC and disables header compatibility
    Given no authentication mode is configured
    When authentication settings are parsed
    Then OIDC authentication is required and headers cannot supply scope

  Scenario: Local authentication explicitly enables header compatibility
    Given local authentication mode is configured
    When authentication settings are parsed
    Then local header compatibility is enabled

  Scenario: A direct invalid authentication mode is rejected
    When an invalid authentication mode is constructed directly
    Then authentication settings reject the invalid mode
