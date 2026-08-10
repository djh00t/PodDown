Feature: Preview Markdown locally
  PodDown previews canonical Markdown deterministically without provider work.

  Scenario: Preview a valid local Markdown source without provider work
    Given a valid local Markdown source and profile configuration
    When the Markdown source is previewed
    Then the preview reports the exact source SHA-256 and resolved profile
    And the preview preserves the original source bytes
    And the preview reports zero provider calls

  Scenario: Emit deterministic compact JSON preview output
    Given a valid local Markdown source and profile configuration
    When the Markdown source is previewed as JSON twice
    Then both JSON previews are byte-identical compact sorted JSON

  Scenario: Reject invalid PodDown frontmatter with validation exit code
    Given a Markdown source with invalid PodDown frontmatter
    When the preview command runs
    Then the preview command exits with validation code 2
    And the preview command reports the stable validation error "Unknown PodDown key: unexpected"

  Scenario: Reject an unknown profile with validation exit code
    Given a Markdown source with an unknown profile
    When the preview command runs
    Then the preview command exits with validation code 2
    And the preview command reports the stable validation error "Unknown profile: missing-profile"

  Scenario: Prefer the profile flag over every other source
    Given profile values at the flag, document, project, and user levels
    When the Markdown source is previewed with the profile flag
    Then the resolved preview profile is "flag-profile"

  Scenario: Prefer the document profile over project and user configuration
    Given profile values at the document, project, and user levels
    When the Markdown source is previewed without the profile flag
    Then the resolved preview profile is "document-profile"

  Scenario: Prefer the project profile over user configuration
    Given profile values at the project and user levels
    When the Markdown source is previewed without the profile flag
    Then the resolved preview profile is "project-profile"

  Scenario: Prefer the user profile over the default profile
    Given a profile value at the user level
    When the Markdown source is previewed without the profile flag
    Then the resolved preview profile is "user-profile"

  Scenario: Fall back to the default profile
    Given only the default local profile is configured
    When the Markdown source is previewed without the profile flag
    Then the resolved preview profile is "default"
