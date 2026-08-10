Feature: Signal and Supply customer-one integration
  Signal and Supply remains a data-only extension of domain-neutral PodDown.

  Scenario: Prepare the finance technology article through public contracts
    Given the versioned Signal and Supply fixture
    When the article is loaded through PodDown public contracts
    Then its source snapshot is preserved exactly
    And no provider or renderer call is made

  Scenario: Preserve finance critical tokens exactly
    Given the versioned Signal and Supply fixture
    When critical tokens are extracted from the article
    Then every declared finance critical token is present with fidelity 1.0

  Scenario: Preserve counter-thesis and uncertainty without hype
    Given the versioned Signal and Supply fixture
    When the article is loaded through PodDown public contracts
    Then the counter-thesis and uncertainty language remain present
    And no unsupported promotional claim is introduced

  Scenario: Apply synthetic presenter disclosure and approved assets
    Given the versioned Signal and Supply fixture
    When the integration metadata is loaded
    Then the presenter disclosure is required in spoken and show-note output
    And every voice asset is synthetic, approved, and demo-only

  Scenario: Keep the robotics fixture unchanged
    Given the versioned Signal and Supply fixture
    When the existing robotics fixture is loaded
    Then its source bytes and digest remain unchanged
