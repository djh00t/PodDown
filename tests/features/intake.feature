Feature: Accept canonical Markdown for an episode
  PodDown validates ordinary Markdown and resolves its profile before paid work.

  Scenario: Accept valid Markdown with a profile
    Given a Markdown document with the profile "technical-dialogue"
    And the profile "technical-dialogue" exists
    When the episode source is validated
    Then the immutable source snapshot is accepted
    And no voice provider has been called

  Scenario: Reject an unknown PodDown frontmatter key
    Given a Markdown document with an unknown PodDown key
    When the episode source is validated
    Then validation fails before any voice provider call
