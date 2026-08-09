"""Adversarial M1 evals for deterministic source-bound content preparation."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from poddown.content import (
    Profile,
    ScriptTurn,
    SourceAnchor,
    SpeakerProfile,
    VoiceAsset,
    VoiceConsent,
)
from poddown.content.adaptation import (
    AdaptationError,
    AdaptationProposal,
    EpisodeTreatment,
    FixtureReasoningPort,
    adapt_source,
)
from poddown.content.lexicon import PronunciationEntry, PronunciationLexicon
from poddown.content.segmentation import SegmentationCapabilities
from poddown.content.service import ContentPreparationRequest, prepare_content
from poddown.content.source import snapshot_source
from poddown.content.tokens import extract_critical_tokens

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "content"
SOURCE = (FIXTURES / "robotics-mapping.md").read_text(encoding="utf-8")
PROFILE_YAML = """profile_id: technical-dialogue
version: 1.0.0
format_type: dialogue
target_minutes: 12
speakers:
  - speaker_id: spk-archivist-1
    display_name: Archivist
    voice_asset_id: voice-archivist
  - speaker_id: spk-controls-2
    display_name: Controls
    voice_asset_id: voice-controls
style: {}
audio: {}
quality: {}
document_overridable: []
"""
ADVERSARIAL = json.loads(
    (FIXTURES / "adversarial-adaptation.json").read_text(encoding="utf-8")
)
TURN_TEXT = {
    "t-001": (
        "On 2026-07-31 at 09:00 UTC, the mobile platform maps a 12-minute field "
        "pass.\nThe mapping rig references LiDAR and C1 and keeps obstacle fusion at "
        "13.8 hertz."
    ),
    "t-002": (
        "During the test window, EKF localization reports 99.7% positional "
        "consistency.\nThe same pass also samples 1.2 km and 0.4 meters vibration "
        "response."
    ),
    "t-003": (
        "The controller is not silent and it is not stable in high-frequency tests."
    ),
    "t-004": (
        "I disagree with the earlier simplification that this architecture can split "
        "the render path\ninto independent streams without temporal synchronization."
    ),
}
TURN_SOURCE_TEXT = {
    turn_id: "\n".join(f"    {line}" for line in text.splitlines())
    for turn_id, text in TURN_TEXT.items()
}
TURN_SOURCE_TEXT["t-001"] = (
    TURN_SOURCE_TEXT["t-001"].replace("LiDAR", "**LiDAR**").replace("C1", "**C1**")
)


class _RendererSentinel:
    """Fail immediately if preparation attempts to reach a renderer boundary."""

    calls = 0

    def __getattr__(self, name: str) -> object:
        self.calls += 1
        raise AssertionError(f"renderer access is forbidden: {name}")


def _anchor(source: str, text: str) -> SourceAnchor:
    snapshot = snapshot_source(source)
    encoded = source.encode("utf-8")
    start = encoded.index(text.encode("utf-8"))
    end = start + len(text.encode("utf-8"))
    block = next(
        block for block in snapshot.blocks if block.start <= start < end <= block.end
    )
    return SourceAnchor(block.block_id, start, end)


def _robotics_request(
    replacements: dict[str, str] | None = None,
) -> ContentPreparationRequest:
    replacements = replacements or {}
    snapshot = snapshot_source(SOURCE)
    anchors = {
        turn_id: _anchor(SOURCE, text) for turn_id, text in TURN_SOURCE_TEXT.items()
    }
    treatment = EpisodeTreatment(
        "robotics-adversarial-eval",
        "dialogue",
        ("evidence", "challenge"),
        12,
        ("overview", "localization", "controls", "disagreement"),
        {"spk-archivist-1": "host", "spk-controls-2": "analyst"},
        tuple(anchors.values()),
        tuple(TURN_TEXT),
    )
    speaker_ids = (
        "spk-archivist-1",
        "spk-controls-2",
        "spk-controls-2",
        "spk-archivist-1",
    )
    turns = tuple(
        ScriptTurn(
            turn_id,
            speaker_id,
            replacements.get(turn_id, text),
            "factual",
            (anchors[turn_id],),
            (anchors[turn_id],),
        )
        for (turn_id, text), speaker_id in zip(
            TURN_TEXT.items(), speaker_ids, strict=True
        )
    )
    proposal = AdaptationProposal(treatment, turns)
    return ContentPreparationRequest(
        SOURCE,
        PROFILE_YAML,
        treatment,
        FixtureReasoningPort({snapshot.source_sha256: proposal}, {}),
        {},
        SegmentationCapabilities(240, None, frozenset(speaker_ids)),
        (
            VoiceAsset("voice-archivist", True),
            VoiceAsset("voice-controls", True),
        ),
        (
            VoiceConsent("voice-archivist", True),
            VoiceConsent("voice-controls", True),
        ),
    )


@pytest.mark.parametrize(
    "mutation", ADVERSARIAL["mutations"], ids=lambda item: item["id"]
)
def test_adversarial_literal_mutations_fail_closed_before_renderer(
    mutation: dict[str, str],
):
    """Removing claim-token validation would accept an unsupported factual mutation."""
    renderer = _RendererSentinel()
    request = _robotics_request({mutation["turn_id"]: mutation["replace"]})

    with pytest.raises(AdaptationError) as error:
        prepare_content(request, renderer=renderer)

    assert error.value.code == mutation["expected_code"]
    assert SOURCE not in str(error.value)
    assert mutation["replace"] not in str(error.value)
    assert renderer.calls == 0


def test_adversarial_anchor_tampering_has_a_safe_stable_error_without_renderer():
    """Skipping anchor-range validation would accept an out-of-source claim."""
    renderer = _RendererSentinel()
    request = _robotics_request()
    proposal = next(iter(request.reasoning.proposals.values()))
    tampered = replace(
        proposal.turns[0],
        claim_anchors=(
            SourceAnchor(proposal.turns[0].claim_anchors[0].block_id, 0, 1),
        ),
    )
    tampered_proposal = replace(proposal, turns=(tampered, *proposal.turns[1:]))
    tampered_request = replace(
        request,
        reasoning=FixtureReasoningPort(
            {snapshot_source(SOURCE).source_sha256: tampered_proposal}, {}
        ),
    )

    with pytest.raises(AdaptationError) as error:
        prepare_content(tampered_request, renderer=renderer)

    assert error.value.code == "missing_anchor"
    assert SOURCE not in str(error.value)
    assert renderer.calls == 0


def test_code_and_table_source_blocks_reject_changed_literals():
    """Dropping source-token checks would allow changed values from code or tables."""
    source = """```text
rate = 13.8 hertz
```

| status | value |
| --- | --- |
| controller | not stable |
"""
    snapshot = snapshot_source(source)
    code, table = snapshot.blocks
    profile = Profile(
        "adversarial-narration",
        "1",
        "narration",
        1,
        (SpeakerProfile("narrator", "Narrator", "asset"),),
        {},
        {},
        {},
        frozenset(),
    )
    treatment = EpisodeTreatment(
        "code-table",
        "narration",
        ("evidence",),
        1,
        ("source",),
        {"narrator": "narrator"},
        (
            SourceAnchor(code.block_id, code.start, code.end),
            SourceAnchor(table.block_id, table.start, table.end),
        ),
        ("code", "table"),
    )
    code_anchor = SourceAnchor(code.block_id, code.start, code.end)
    table_anchor = SourceAnchor(table.block_id, table.start, table.end)
    proposal = AdaptationProposal(
        treatment,
        (
            ScriptTurn(
                "code",
                "narrator",
                "rate = 13.9 hertz",
                "factual",
                (code_anchor,),
                (code_anchor,),
            ),
            ScriptTurn(
                "table",
                "narrator",
                "controller stable",
                "factual",
                (table_anchor,),
                (table_anchor,),
            ),
        ),
    )

    with pytest.raises(AdaptationError) as error:
        adapt_source(
            snapshot,
            profile,
            treatment,
            FixtureReasoningPort({snapshot.source_sha256: proposal}, {}),
        )

    assert {block.kind for block in (code, table)} == {"code", "table"}
    assert error.value.code == "unsupported_claim"
    assert source not in str(error.value)


def test_homographs_and_repeated_negations_keep_distinct_occurrences():
    """Collapsing identical surfaces would lose homograph or negation evidence."""
    lexicon = PronunciationLexicon(
        "episode",
        "1",
        (PronunciationEntry("lead", "lead", "led", "1", "technical_term"),),
    )

    tokens = extract_critical_tokens(
        ADVERSARIAL["homograph_text"], {"episode": lexicon}
    )

    lead_tokens = [token for token in tokens if token.normalized == "lead"]
    negations = [token for token in tokens if token.category == "negation"]
    assert [token.occurrence_id for token in lead_tokens] == ["tok-01", "tok-02"]
    assert len({token.source_span for token in lead_tokens}) == 2
    assert [token.occurrence_id for token in negations] == ["neg-01", "neg-02"]


def test_source_anchor_tamper_fixture_is_valid_json_and_names_required_corpus():
    """A malformed corpus could make an adversarial evaluation silently disappear."""
    assert ADVERSARIAL["schema_version"] == 1
    assert {mutation["id"] for mutation in ADVERSARIAL["mutations"]} == {
        "absent-plausible-claim",
        "changed-number",
        "changed-unit",
        "changed-date",
        "inserted-negation",
        "removed-negation",
    }
