Feature: explicit runtime workflow snapshot composition

  Scenario: compose a source-bound reference snapshot from a configured fixture
    Given an explicit reference workflow snapshot factory
    When I build a create workflow snapshot for the reference episode
    Then the snapshot preserves the source hash and configured host-local binding

  Scenario: reject a source that is not the configured fixture
    Given an explicit reference workflow snapshot factory
    When I build a snapshot for a different source
    Then runtime snapshot composition fails closed

  Scenario: reject a command mode that differs from the configured fixture mode
    Given an explicit reference workflow snapshot factory
    When I build a host-local snapshot with a deterministic-local command mode
    Then runtime snapshot composition fails closed

  Scenario: bind critical tokens only to their source turns
    Given an explicit deterministic reference workflow snapshot factory
    When I build a create workflow snapshot for the reference episode
    Then each snapshot segment carries only its source turn critical tokens
