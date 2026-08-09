# Specification: Content Intelligence

**Status:** Planned

## Goal

Transform an immutable Markdown source snapshot into a versioned, source-bound,
audio-ready canonical script without inventing factual claims.

## Contracts

- `EpisodeTreatment` defines format, narrative arc, target duration, sections,
  speaker roles and source anchors; it never becomes renderable text.
- `ScriptVersion` contains ordered immutable `ScriptTurn` values with stable IDs,
  speaker, text, kind (`factual` or `editorial`), source anchors and claim anchors.
- Every factual sentence has at least one valid anchor to a byte range or block ID
  in the source package. Editorial turns contain no externally verifiable claim.
- Adaptation uses a structured-output reasoning port. Provider output is schema
  validated, anchor checked and duration bounded before persistence.
- Unsupported, contradictory or unanchored claims fail adaptation. Repair may
  rewrite only the failing turn and must preserve all accepted turn IDs.

## Pronunciation

Lexicons are immutable versions layered episode, project, domain and global.
Resolution uses Unicode-normalized exact keys; highest priority wins and conflicts
at the same priority fail. Each resolution records source lexicon and entry IDs.
No learned correction mutates a published version; approval creates a new version.

Critical-token extraction covers names, organizations, products, acronyms,
technical terms, numbers, currencies, percentages, dates, units, ticker-like
symbols and every negation occurrence. Each token has source span, script span,
normalized form, expected spoken form, category and occurrence ID.

## Segmentation

Segments contain contiguous complete turns, never split a critical token, preserve
topic/source groupings, and satisfy renderer capability limits. They include
unspoken leading/trailing continuity context, estimated duration and difficulty.
The same inputs and capability set produce the same segmentation manifest.

## Acceptance behavior

1. A difficult technical article produces a two-speaker 10–15 minute script with
   stable turns and complete factual anchors.
2. An invented fact, unsupported comparison or changed number is rejected before
   paid rendering.
3. Dialogue contains meaningful challenge and disagreement without repetitive
   filler or synthetic enthusiasm.
4. Layered pronunciation resolves deterministically and records every version.
5. All critical-token categories and repeated negations are extracted correctly.
6. Segments meet provider limits while preserving token and turn boundaries.
7. Re-running identical inputs produces an equivalent canonical script manifest.

## Evals

Use adversarial source fixtures containing absent-but-plausible claims,
contradictions, tables, code, citations, homographs and repeated numbers. Require
100% unsupported-claim rejection on the critical eval set, 100% critical-token
recall, and human rubric scores of at least 4/5 for structure, natural dialogue,
non-repetition and source faithfulness.

