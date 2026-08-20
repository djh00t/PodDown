Feature: Durable publication service receipts
  Publication dispatches retain replay-safe receipt evidence.

  Scenario: A reconstructed service replays a durable successful receipt
    Given a filesystem publication stored with a durable receipt repository
    When a reconstructed service requests the same publication
    Then the stored receipt is returned without another filesystem dispatch

  Scenario: A reconstructed service rejects a changed package identity
    Given a filesystem publication stored with a durable receipt repository
    When a reconstructed service requests a changed package identity
    Then the durable receipt identity conflict is reported

  Scenario: A pre-dispatch artifact failure can recover after reconstruction
    Given a durable publication attempt ready for pre-dispatch recovery
    When a temporary artifact read failure occurs before dispatch
    And a reconstructed service retries the safe failure
    Then the reconstructed publication is resumed

  Scenario: A reconstructed service retries a known-not-applied outcome
    Given a filesystem adapter reports a known-not-applied outcome
    When a reconstructed service retries the safe failure
    Then the reconstructed publication is resumed

  Scenario: An uncertain outcome blocks reconstructed retry
    Given a durable uncertain publication outcome
    When a reconstructed service retries the uncertain publication
    Then no second filesystem dispatch is attempted

  Scenario: A compensated filesystem failure is retryable
    Given a filesystem adapter reports a compensated failure
    When a reconstructed service retries the safe failure
    Then the reconstructed publication is resumed

  Scenario: Provider credentials are redacted from durable errors
    Given a provider error includes quoted JSON, form, nested credentials, and structured headers
    When the service records the uncertain outcome
    Then durable and exposed errors contain no credential values
    And a reconstructed retry is blocked without a second dispatch

  Scenario: Composite publication scope separates the same key by tenant and project
    Given a filesystem publication stored with a durable receipt repository
    When the same episode and target key is used for another tenant and then another project
    Then all composite scopes dispatch independently
