# Reasoning model selection

`select_reasoning_model()` is the production configuration boundary for the
fixture-backed R08 source-fidelity corpus. It accepts the validated corpus and
configured `ReasoningModelCandidate` values, then selects the lowest configured
cost candidate that passes every source-fidelity gate. Equal-cost candidates
retain R08's lexical model-ID tie-break.

The returned `ReasoningModelSelection` contains only the selected model,
configured cost, and the deterministic evaluation report. `to_record()` is
safe for preparation or runtime composition provenance: it contains no source
payloads, envelope records, credentials, transport, or provider response.

Live runtime composition accepts this `to_record()` projection through
`PODDOWN_OPENAI_REASONING_SELECTION_JSON`. It reconstructs the typed
selection, uses the selected model for the OpenAI Responses transport, and
rejects malformed or internally inconsistent records before provider dispatch.
An optional `PODDOWN_OPENAI_REASONING_MODEL` value is checked against the
selection rather than used to choose a different model.

Missing corpus coverage, invalid candidate configuration, and a corpus with no
fully passing candidate all raise `ReasoningSelectionError`. The boundary makes
no provider calls and does not change fixture-backed or local preparation
defaults. A caller that is separately authorized to compose a live transport
uses `selection.model` as its explicit model identifier.
