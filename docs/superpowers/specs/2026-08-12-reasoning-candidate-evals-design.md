# Deterministic reasoning-candidate evaluations

## Scope

R08 evaluates configured reasoning-model candidates with deterministic fixture
responses and selects the cheapest candidate that passes every case. It does
not create a live provider transport or alter V11 files.

## Design

The runner receives a source, profile, treatment, and ordered candidate
fixtures. Each fixture contains a model identifier, configured cost, and an
adaptation-envelope record. It parses the record with the immutable envelope
validator, converts the parsed turns to the existing adaptation proposal, and
calls `adapt_source`. This reuses the established source-anchor, critical-token,
negation, speaker, and dialogue validation instead of duplicating it.

Each candidate produces an immutable result per corpus case with the four
source-fidelity gate outcomes and a stable failure code. A candidate passes
only when every case passes. The selector chooses the lowest configured cost,
then lexical model identifier to break equal-cost ties. The evidence record
contains every configured candidate in input order and serializes through
canonical JSON (sorted keys and compact separators), so repeated runs have
identical bytes. Invalid configuration or envelope identity mismatches fail
closed. No runner code imports, instantiates, or calls a provider transport.

## Tests

BDD covers a passing source-faithful candidate and a failing candidate with a
negation change. Focused unit tests cover stable serialized evidence, envelope
identity validation, and source/dialogue validation reuse.
