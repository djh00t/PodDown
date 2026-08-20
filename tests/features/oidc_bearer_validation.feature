Feature: Offline OIDC bearer-token validation
  The API authentication boundary verifies token authority without trusting request context headers.

  Scenario: A valid signed bearer token supplies verified principal input
    Given an offline OIDC bearer-token validator
    And a valid signed bearer token
    When the bearer token is validated
    Then verified subject and issuer claims are returned

  Scenario: An invalid signature fails closed without credential details
    Given an offline OIDC bearer-token validator
    And a bearer token signed by an untrusted key
    When the bearer token is validated
    Then a safe invalid bearer-token error is returned
