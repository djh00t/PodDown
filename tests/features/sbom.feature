Feature: deterministic dependency SBOM
  Release evidence includes the exact locked dependency graph.

  Scenario: A lockfile produces a reproducible CycloneDX SBOM
    Given a small authoritative uv lockfile
    When I generate the dependency SBOM twice
    Then the SBOMs are byte-identical and include dependency hashes
