"""Tests for source-bound dialogue adaptation and single-turn repair."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from poddown.content.models import Profile, SourceAnchor, SpeakerProfile
from poddown.content.source import snapshot_source

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "content"
SOURCE = (FIXTURES_DIR / "robotics-mapping.md").read_text(encoding="utf-8")
OVERVIEW = (
    "On 2026-07-31 at 09:00 UTC, the mobile platform maps a 12-minute field pass."
)
LOCALIZATION = (
    "During the test window, EKF localization reports 99.7% positional consistency."
)
CONTROLS = "The controller is not silent and it is not stable in high-frequency tests."
DISAGREEMENT = (
    "I disagree with the earlier simplification that this architecture can split the "
    "render path"
)


def _profile() -> Profile:
    return Profile(
        "technical-dialogue",
        "1.0.0",
        "dialogue",
        12,
        (
            SpeakerProfile("spk-archivist-1", "Archivist", "voice-archivist"),
            SpeakerProfile("spk-controls-2", "Controls", "voice-controls"),
        ),
        {"tone": "precise"},
        {},
        {},
        frozenset(),
    )


def _anchor(snapshot, text: str) -> SourceAnchor:
    source_bytes = snapshot.source.encode("utf-8")
    fragment = text.encode("utf-8")
    start = source_bytes.index(fragment)
    end = start + len(fragment)
    block = next(
        block for block in snapshot.blocks if block.start <= start <= end <= block.end
    )
    return SourceAnchor(block.block_id, start, end)


def _proposal(snapshot, treatment):
    from poddown.content.adaptation import AdaptationProposal
    from poddown.content.models import ScriptTurn

    overview = _anchor(snapshot, OVERVIEW)
    localization = _anchor(snapshot, LOCALIZATION)
    controls = _anchor(snapshot, CONTROLS)
    disagreement = _anchor(snapshot, DISAGREEMENT)
    return AdaptationProposal(
        treatment,
        (
            ScriptTurn(
                "t-robotics-001",
                "spk-archivist-1",
                OVERVIEW,
                "factual",
                (overview,),
                (overview,),
            ),
            ScriptTurn(
                "t-robotics-002",
                "spk-controls-2",
                LOCALIZATION,
                "factual",
                (localization,),
                (localization,),
            ),
            ScriptTurn(
                "t-robotics-003",
                "spk-controls-2",
                CONTROLS,
                "factual",
                (controls,),
                (controls,),
            ),
            ScriptTurn(
                "t-robotics-004",
                "spk-archivist-1",
                DISAGREEMENT,
                "factual",
                (disagreement,),
                (disagreement,),
            ),
        ),
    )


def _treatment(snapshot):
    from poddown.content.adaptation import EpisodeTreatment

    return EpisodeTreatment(
        "robotics-mapping",
        "dialogue",
        ("evidence", "challenge"),
        12,
        ("overview", "localization", "controls", "disagreement"),
        {"spk-archivist-1": "host", "spk-controls-2": "analyst"},
        tuple(
            SourceAnchor(block.block_id, block.start, block.end)
            for block in snapshot.blocks
        ),
        ("t-robotics-001", "t-robotics-002", "t-robotics-003", "t-robotics-004"),
    )


def _adapt(snapshot, proposal, treatment):
    from poddown.content.adaptation import FixtureReasoningPort, adapt_source

    return adapt_source(
        snapshot,
        _profile(),
        treatment,
        FixtureReasoningPort({snapshot.source_sha256: proposal}, {}),
    )


def test_adapt_source_accepts_real_fixture_turns_with_anchors_and_disagreement():
    """Dropping source validation would accept a proposal without anchored dialogue."""
    snapshot = snapshot_source(SOURCE)
    treatment = _treatment(snapshot)
    script = _adapt(snapshot, _proposal(snapshot, treatment), treatment)

    assert tuple(turn.turn_id for turn in script.turns) == treatment.expected_turn_ids
    assert {turn.speaker_id for turn in script.turns} == {
        "spk-archivist-1",
        "spk-controls-2",
    }
    assert "I disagree" in script.turns[-1].text
    assert script.source_sha256 == snapshot.source_sha256


@pytest.mark.parametrize(
    ("turn_index", "text"),
    [
        (
            1,
            LOCALIZATION.replace("99.7%", "98.7%"),
        ),
        (2, "The controller is silent and it is not stable in high-frequency tests."),
        (
            1,
            LOCALIZATION.replace("reports", "reports a more precise"),
        ),
    ],
)
def test_adapt_source_rejects_changed_literals_and_unsupported_comparisons(
    turn_index, text
):
    """A changed number, negation, or comparison must fail before rendering."""
    from poddown.content.adaptation import AdaptationError

    snapshot = snapshot_source(SOURCE)
    treatment = _treatment(snapshot)
    proposal = _proposal(snapshot, treatment)
    turns = list(proposal.turns)
    turns[turn_index] = replace(turns[turn_index], text=text)

    with pytest.raises(AdaptationError) as error:
        _adapt(snapshot, replace(proposal, turns=tuple(turns)), treatment)

    assert error.value.code == "unsupported_claim"
    assert SOURCE not in error.value.detail
    assert "99.7% positional consistency" not in error.value.detail


def test_adapt_source_rejects_missing_anchors_and_unknown_speakers():
    """A missing evidence reference or unapproved speaker must not enter a script."""
    from poddown.content.adaptation import AdaptationError

    snapshot = snapshot_source(SOURCE)
    treatment = _treatment(snapshot)
    proposal = _proposal(snapshot, treatment)
    missing = replace(proposal.turns[0], claim_anchors=())
    unknown = replace(proposal.turns[1], speaker_id="unapproved")

    with pytest.raises(AdaptationError) as missing_error:
        _adapt(
            snapshot, replace(proposal, turns=(missing, *proposal.turns[1:])), treatment
        )
    with pytest.raises(AdaptationError) as speaker_error:
        _adapt(
            snapshot,
            replace(proposal, turns=(proposal.turns[0], unknown, *proposal.turns[2:])),
            treatment,
        )

    assert missing_error.value.code == "missing_anchor"
    assert speaker_error.value.code == "invalid_speaker"


def test_adapt_source_requires_canonical_turn_ids_two_speakers_and_disagreement():
    """Duplicate IDs, single-speaker dialogue, and filler-only dialogue are invalid."""
    from poddown.content.adaptation import AdaptationError

    snapshot = snapshot_source(SOURCE)
    treatment = _treatment(snapshot)
    proposal = _proposal(snapshot, treatment)
    duplicate = replace(proposal.turns[1], turn_id="t-robotics-001")
    one_speaker = tuple(
        replace(turn, speaker_id="spk-archivist-1") for turn in proposal.turns
    )
    no_challenge = replace(
        proposal.turns[-1], text="This architecture can split the render path"
    )

    for turns in (
        (proposal.turns[0], duplicate, *proposal.turns[2:]),
        one_speaker,
        (*proposal.turns[:-1], no_challenge),
    ):
        with pytest.raises(AdaptationError) as error:
            _adapt(snapshot, replace(proposal, turns=turns), treatment)
        assert error.value.code == "dialogue_quality"


def test_adapt_source_rejects_treatment_duration_mismatch():
    """A proposal cannot silently bypass the profile's approved target duration."""
    from poddown.content.adaptation import AdaptationError

    snapshot = snapshot_source(SOURCE)
    treatment = replace(_treatment(snapshot), target_minutes=11)

    with pytest.raises(AdaptationError) as error:
        _adapt(snapshot, _proposal(snapshot, treatment), treatment)

    assert error.value.code == "duration"


def test_repair_turn_replaces_only_the_named_turn_and_preserves_anchors_and_order():
    """A repair that alters another accepted turn or its evidence must fail closed."""
    from poddown.content.adaptation import AdaptationError, repair_turn

    snapshot = snapshot_source(SOURCE)
    treatment = _treatment(snapshot)
    script = _adapt(snapshot, _proposal(snapshot, treatment), treatment)
    replacement = replace(
        script.turns[1],
        text=LOCALIZATION,
    )

    repaired = repair_turn(script, "t-robotics-002", replacement, snapshot, _profile())

    assert tuple(turn.turn_id for turn in repaired.turns) == tuple(
        turn.turn_id for turn in script.turns
    )
    assert repaired.turns[0] is script.turns[0]
    assert repaired.turns[2:] == script.turns[2:]
    with pytest.raises(AdaptationError) as error:
        repair_turn(
            script,
            "t-robotics-002",
            replace(replacement, source_anchors=()),
            snapshot,
            _profile(),
        )
    assert error.value.code == "missing_anchor"


def test_fixture_reasoning_port_is_source_and_turn_keyed_without_renderer_calls():
    """The deterministic adapter must not need a renderer or accept a wrong source."""
    from poddown.content.adaptation import FixtureReasoningPort

    snapshot = snapshot_source(SOURCE)
    treatment = _treatment(snapshot)
    proposal = _proposal(snapshot, treatment)
    port = FixtureReasoningPort(
        {snapshot.source_sha256: proposal}, {"t-robotics-002": proposal.turns[1]}
    )

    assert port.adapt(snapshot, _profile(), treatment) is proposal
    assert (
        port.repair(snapshot, _profile(), proposal.turns[1], "unsupported_claim")
        == proposal.turns[1]
    )
    with pytest.raises(LookupError):
        port.adapt(snapshot_source("# another source\n"), _profile(), treatment)
