Feature: Guarded one-segment live-provider smoke
  Live smoke is an explicit, bounded provider-render and provider-ASR check.

  Scenario: Render and transcribe one approved live segment
    Given a fully configured live-provider smoke environment
    When one live segment is rendered and transcribed
    Then provider render and ASR evidence pass the critical-token gate
