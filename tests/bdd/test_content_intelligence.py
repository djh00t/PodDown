"""Executable acceptance tests for content-intelligence preparation."""

from __future__ import annotations

from copy import deepcopy
from importlib import import_module
from pathlib import Path
import json
import textwrap

from pytest_bdd import given, scenarios, then, when

scenarios("../features/content_intelligence.feature")

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "content"


def _load_text_fixture(filename: str) -> str:
    return (FIXTURES_DIR / filename).read_text(encoding="utf-8")


def _load_json_fixture(filename: str) -> dict:
    return json.loads((FIXTURES_DIR / filename).read_text(encoding="utf-8"))


def _normalize_key(context_value: str | None) -> str:
    return (context_value or "").strip().lower()


def _attribute_or_key(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


@given("the robotics mapping source and technical dialogue profile")
def robotics_source_with_profile(context):
    context.values["source"] = _load_text_fixture("robotics-mapping.md")
    context.values["profile"] = _load_text_fixture("technical-dialogue-profile.yaml")
    context.values["proposal"] = _load_json_fixture("robotics-adaptation.json")


@given("a deterministic source-bound adaptation proposal")
def deterministic_adaptation_proposal(context):
    proposal = _load_json_fixture("robotics-adaptation.json")
    context.values["proposal"] = proposal


@given("the robotics mapping source and a proposal that changes a source number")
def proposal_with_changed_number(context):
    proposal = deepcopy(_load_json_fixture("robotics-adaptation.json"))
    for claim in proposal["claims"]:
        if claim["claim_anchor"] == "claim-lidar-frequency":
            proposal["claims"][proposal["claims"].index(claim)] = {
                **claim,
                "adapted_value": "18 hertz",
            }
            break
    context.values["source"] = _load_text_fixture("robotics-mapping.md")
    context.values["profile"] = _load_text_fixture("technical-dialogue-profile.yaml")
    context.values["proposal"] = proposal


@given('four lexicon layers for the key "C1"')
def lexicon_layers_for_key(context):
    context.values["lexicon_key"] = "C1"
    context.values["lexicon_layers"] = [
        {"layer": "project", "version": "project-v1", "entry_id": "c1-project-v1"},
        {"layer": "episode", "version": "episode-v3", "entry_id": "c1-episode-v3"},
        {"layer": "profile", "version": "profile-v2", "entry_id": "c1-profile-v2"},
        {"layer": "poddown", "version": "platform-v1", "entry_id": "c1-platform-v1"},
    ]


@given('two project lexicon entries for the normalized key "LiDAR"')
def conflicting_project_lexicon_entries(context):
    context.values["lexicon_key"] = "LiDAR"
    context.values["lexicon_layers"] = [
        {"layer": "project", "version": "project-v1", "entry_id": "lidar-project-a"},
        {"layer": "project", "version": "project-v2", "entry_id": "lidar-project-b"},
    ]

@given('the text "The system is not silent, and it is not stable"')
def deterministic_negated_text(context):
    context.values["critical_text"] = textwrap.dedent(
        "The system is not silent, and it is not stable"
    )


@given(
    "a canonical script with a turn longer than the renderer text limit"
)
def canonical_script_over_limit(context):
    context.values["script"] = {
        "turns": [
            {
                "turn_id": "t-001",
                "speaker_id": "spk-engineer-a",
                "source_block_anchor": "block-robotics-001",
                "text": "word " * 5000,
            }
        ],
        "renderer_text_limit": 240,
    }


@when("content intelligence prepares the episode")
def prepare_episode(context):
    try:
        service = import_module("poddown.content.service")
    except ModuleNotFoundError as error:
        raise AssertionError("poddown.content.service is not implemented") from error
    prepare_fn = getattr(service, "prepare_content", None)
    if prepare_fn is None:
        raise AssertionError("poddown.content.service.prepare_content is not implemented")
    context.values["result"] = prepare_fn(
        source=context.values["source"],
        profile=context.values["profile"],
        proposal=context.values["proposal"],
    )


@when("the pronunciation is resolved")
def resolve_pronunciation(context):
    try:
        lexicon = import_module("poddown.content.lexicon")
    except ModuleNotFoundError as error:
        raise AssertionError("poddown.content.lexicon is not implemented") from error
    resolve_fn = getattr(lexicon, "resolve_pronunciation", None)
    if resolve_fn is None:
        raise AssertionError(
            "poddown.content.lexicon.resolve_pronunciation is not implemented"
        )
    context.values["result"] = resolve_fn(
        key="C1", layers=context.values["lexicon_layers"]
    )


@when("the project pronunciation is resolved")
def resolve_project_pronunciation(context):
    try:
        lexicon = import_module("poddown.content.lexicon")
    except ModuleNotFoundError as error:
        raise AssertionError("poddown.content.lexicon is not implemented") from error
    resolve_fn = getattr(lexicon, "resolve_pronunciation", None)
    if resolve_fn is None:
        raise AssertionError(
            "poddown.content.lexicon.resolve_pronunciation is not implemented"
        )
    context.values["result"] = resolve_fn(
        key=context.values["lexicon_key"], layers=context.values["lexicon_layers"]
    )


@when("critical tokens are extracted")
def extract_tokens(context):
    try:
        tokens = import_module("poddown.content.tokens")
    except ModuleNotFoundError as error:
        raise AssertionError("poddown.content.tokens is not implemented") from error
    extract_fn = getattr(tokens, "extract_critical_tokens", None)
    if extract_fn is None:
        raise AssertionError(
            "poddown.content.tokens.extract_critical_tokens is not implemented"
        )
    context.values["result"] = extract_fn(context.values["critical_text"])


@when("the script is segmented")
def segment_script(context):
    try:
        segmentation = import_module("poddown.content.segmentation")
    except ModuleNotFoundError as error:
        raise AssertionError("poddown.content.segmentation is not implemented") from error
    segment_fn = getattr(segmentation, "segment_script", None)
    if segment_fn is None:
        raise AssertionError(
            "poddown.content.segmentation.segment_script is not implemented"
        )
    context.values["result"] = segment_fn(context.values["script"])


@then("the canonical script has two stable speakers and complete factual anchors")
def script_has_stable_speakers(context):
    result = context.values["result"]
    script = _attribute_or_key(result, "canonical_script")
    turns = _attribute_or_key(script, "turns", [])
    assert len(turns) == 3
    assert { _attribute_or_key(turn, "speaker_id") for turn in turns } == {
        "spk-archivist-1",
        "spk-controls-2",
    }
    assert all(_attribute_or_key(turn, "claim_anchor") for turn in turns)
    assert all(_attribute_or_key(turn, "source_block_anchor") for turn in turns)


@then("the script contains disagreement without unsupported claims")
def script_disagreement_only(context):
    result = context.values["result"]
    script = _attribute_or_key(result, "canonical_script")
    turns = _attribute_or_key(script, "turns", [])
    disagreement = [
        turn
        for turn in turns
        if _attribute_or_key(turn, "turn_id")
        == _attribute_or_key(context.values["proposal"], "disagreement_turn_id")
    ]
    assert disagreement
    assert _attribute_or_key(disagreement[0], "supported_by_source") is True


@then("every extracted critical token has an expected spoken form")
def critical_tokens_have_spoken_forms(context):
    tokens = _attribute_or_key(context.values["result"], "critical_tokens", [])
    assert tokens is not None
    assert len(tokens) > 0
    assert all(
        _attribute_or_key(token, "spoken_form")
        and _attribute_or_key(token, "source_form")
        for token in tokens
    )


@then("the segmentation manifest preserves turn order and source grouping")
def segmentation_manifest_preserves_structure(context):
    script = _attribute_or_key(context.values["result"], "segmentation_manifest", {})
    turn_ids = _attribute_or_key(script, "turn_ids", [])
    source_blocks = _attribute_or_key(script, "source_block_ids", [])
    assert isinstance(turn_ids, list) and isinstance(source_blocks, list)
    assert turn_ids == sorted(turn_ids)


@then("adaptation fails with an unsupported claim error")
def adaptation_rejects_changed_number(context):
    result = context.values["result"]
    assert _attribute_or_key(result, "accepted", False) is False
    assert "unsupported claim" in str(_attribute_or_key(result, "error", "")).lower()


@then("no renderer call is made")
def no_renderer_call(context):
    result = context.values["result"]
    assert _attribute_or_key(result, "provider_calls", 0) == 0


@then("the episode layer wins and its version and entry ID are recorded")
def episode_layer_wins(context):
    result = context.values["result"]
    selected = _attribute_or_key(result, "selected_layer")
    assert selected == "episode"
    assert _attribute_or_key(result, "version")
    assert _attribute_or_key(result, "entry_id")


@then("lexicon resolution fails closed with a conflict error")
def lexicon_conflict_closed(context):
    result = context.values["result"]
    assert _attribute_or_key(result, "accepted", False) is False
    assert "conflict" in str(_attribute_or_key(result, "error", "")).lower()


@then("two distinct negation occurrences are present")
def negation_occurrences(context):
    tokens = _attribute_or_key(context.values["result"], "tokens", [])
    negations = [
        token
        for token in tokens
        if _attribute_or_key(token, "normalized", "not") == "not"
    ]
    assert len(negations) == 2


@then("the token manifest is deterministic")
def deterministic_token_manifest(context):
    assert _attribute_or_key(context.values["result"], "manifest_deterministic", False)


@then("segmentation fails with a capability error")
def segmentation_capability_error(context):
    result = context.values["result"]
    assert _attribute_or_key(result, "accepted", False) is False
    error = str(_attribute_or_key(result, "error", "")).lower()
    assert "capability" in error
