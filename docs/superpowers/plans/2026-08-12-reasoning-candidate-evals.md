# Deterministic reasoning-candidate evaluations Implementation Plan

**Goal:** Evaluate fixture-backed reasoning candidates and select the cheapest passing model.

**Architecture:** Parse each fixture envelope and route its turns through the
existing adaptation validator. Immutable results expose source-fidelity gates
and canonical JSON evidence; stable sorting selects a passing candidate.

**Constraints:** no live transport imports or calls; do not change V11 files;
reuse the adaptation-envelope parser and `adapt_source`; sort JSON keys.

## Steps

1. Add failing BDD for a source-faithful candidate and negation failure.
2. Run the BDD to capture the missing evaluator failure.
3. Implement the evaluator, gate evidence, and cheapest-pass selection.
4. Add focused unit cases for stable serialization and model tie-breaking.
5. Run focused tests, Ruff format/check, strict mypy, and inspect the diff.
