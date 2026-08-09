"""Executable acceptance tests for content-intelligence preparation."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from copy import deepcopy
from importlib import import_module
from pathlib import Path

from pytest_bdd import given, scenarios, then, when

scenarios("../features/content_intelligence.feature")

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "content"


def _load_text_fixture(filename: str) -> str:
    return (FIXTURES_DIR / filename).read_text(encoding="utf-8")


def _load_json_fixture(filename: str) -> dict:
    return json.loads((FIXTURES_DIR / filename).read_text(encoding="utf-8"))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _extract_frontmatter(source: str) -> dict:
    lines = source.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}

    fm_lines: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        fm_lines.append(line)

    if not fm_lines:
        return {}

    try:
        import yaml  # type: ignore
    except Exception:  # pragma: no cover - optional dependency path
        return _parse_frontmatter_manual("\n".join(fm_lines))

    parsed = yaml.safe_load("\n".join(fm_lines))
    return parsed or {}


def _parse_frontmatter_manual(frontmatter_text: str) -> dict:
    """Minimal YAML-like parser for known fixture style."""

    root: dict[str, object] = {}
    stack: list[tuple[dict[str, object], int]] = [(root, 0)]

    for raw in frontmatter_text.splitlines():
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        while stack and indent < stack[-1][1]:
            stack.pop()
        current = stack[-1][0]

        m = re.match(r"^(\S[^:]*)\s*:\s*(.*)$", raw.strip())
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        if value == "":
            new: dict[str, object] = {}
            current[key] = new
            stack.append((new, indent + 2))
        else:
            normalized = value
            if value.lower() in {"true", "false"}:
                parsed = value.lower() == "true"
            elif re.fullmatch(r"-?\d+\.\d+", value):
                parsed = float(value)
            elif re.fullmatch(r"-?\d+", value):
                parsed = int(value)
            else:
                parsed = value.strip('"\'')
            current[key] = parsed

    return root


def _attribute_or_key(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _key_values_from(obj, key_path: str):
    current = obj
    for part in key_path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


class RendererProbe:
    """Boundary callback capturing any provider/renderer interaction attempt."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple, dict]] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def __call__(self, *args, **kwargs) -> dict:
        self.calls.append((args, kwargs))
        return {
            "status": "blocked-by-test",
            "provider": _attribute_or_key(kwargs, "provider", "unknown"),
        }


def _invoke_prepare_content(prepare_fn, context):
    source_value = context.values["source"]
    profile_value = context.values["profile"]
    proposal_value = context.values["proposal"]

    signature = inspect.signature(prepare_fn)
    params = signature.parameters

    source_arg = next((name for name in ["source", "source_text", "markdown", "source_markdown", "episode_source"] if name in params), "source")
    profile_arg = next(
        (name for name in ["profile", "profile_text", "profile_config", "profile_yaml", "poddown_profile"] if name in params),
        "profile",
    )
    proposal_arg = next((name for name in ["proposal", "adaptation", "adaptation_proposal", "source_bound_proposal"] if name in params), "proposal")
    boundary_arg = next(
        (name for name in ["renderer", "provider_renderer", "renderer_boundary", "render_boundary", "render_fn", "renderer_fn", "provider_boundary"] if name in params),
        None,
    )

    kwargs = {source_arg: source_value, profile_arg: profile_value, proposal_arg: proposal_value}
    if boundary_arg is not None:
        kwargs[boundary_arg] = context.values["renderer_probe"]

    try:
        return prepare_fn(**kwargs)
    except TypeError as error:
        if "unexpected keyword argument" in str(error):
            return prepare_fn(source_value, profile_value, proposal_value)
        raise


def _expected_proposal(context):
    return context.values["proposal"]


def _expected_critical_tokens(context):
    return _attribute_or_key(_expected_proposal(context), "expected_critical_tokens", [])


def _expected_negation_occurrences():
    text = "The system is not silent, and it is not stable"
    first = text.index("not")
    second = text.rindex("not")
    return [
        {"occurrence_id": "neg-01", "span_start": first, "span_end": first + len("not")},
        {"occurrence_id": "neg-02", "span_start": second, "span_end": second + len("not")},
    ]


given_given_source_profile = "the robotics mapping source and technical dialogue profile"


@given(given_given_source_profile)
def robotics_source_with_profile(context):
    context.values["source"] = _load_text_fixture("robotics-mapping.md")
    context.values["profile"] = _load_text_fixture("technical-dialogue-profile.yaml")
    context.values["proposal"] = _load_json_fixture("robotics-adaptation.json")
    context.values["source_frontmatter"] = _extract_frontmatter(context.values["source"])
    context.values["source_sha256"] = _sha256(context.values["source"])
    context.values["renderer_probe"] = RendererProbe()


@given("a deterministic source-bound adaptation proposal")
def deterministic_adaptation_proposal(context):
    context.values["proposal"] = _load_json_fixture("robotics-adaptation.json")


@given("the robotics mapping source and a proposal that changes a source number")
def proposal_with_changed_number(context):
    proposal = deepcopy(_load_json_fixture("robotics-adaptation.json"))
    for idx, claim in enumerate(proposal["claims"]):
        if claim["claim_anchor"] == "claim-lidar-frequency":
            proposal["claims"][idx] = {**claim, "adapted_value": "18 hertz"}
            break

    context.values["source"] = _load_text_fixture("robotics-mapping.md")
    context.values["profile"] = _load_text_fixture("technical-dialogue-profile.yaml")
    context.values["proposal"] = proposal
    context.values["source_frontmatter"] = _extract_frontmatter(context.values["source"])
    context.values["source_sha256"] = _sha256(context.values["source"])
    context.values["renderer_probe"] = RendererProbe()


@given('four lexicon layers for the key "C1"')
def lexicon_layers_for_key(context):
    context.values["lexicon_key"] = "C1"
    context.values["lexicon_layers"] = [
        {"layer": "global", "version": "global-v1", "entry_id": "c1-global-v1", "spoken_form": "see one global", "scope": "global"},
        {"layer": "domain", "version": "domain-v2", "entry_id": "c1-domain-v2", "spoken_form": "see one domain", "scope": "domain"},
        {"layer": "project", "version": "project-v3", "entry_id": "c1-project-v3", "spoken_form": "see one project", "scope": "project"},
        {"layer": "episode", "version": "episode-v4", "entry_id": "c1-episode-v4", "spoken_form": "see one episode", "scope": "episode"},
    ]


@given('two project lexicon entries for the normalized key "LiDAR"')
def conflicting_project_lexicon_entries(context):
    context.values["lexicon_key"] = "LiDAR"
    context.values["lexicon_layers"] = [
        {"layer": "project", "version": "project-v1", "entry_id": "lidar-project-a", "spoken_form": "LIE-dar", "normalized": "lidar", "scope": "project"},
        {"layer": "project", "version": "project-v2", "entry_id": "lidar-project-b", "spoken_form": "LIE-der", "normalized": "lidar", "scope": "project"},
    ]


@given('the text "The system is not silent, and it is not stable"')
def deterministic_negated_text(context):
    context.values["critical_text"] = "The system is not silent, and it is not stable"
    context.values["expected_negation_occurrences"] = _expected_negation_occurrences()


@given("a canonical script with a turn longer than the renderer text limit")
def canonical_script_over_limit(context):
    context.values["script"] = {
        "turns": [
            {
                "turn_id": "t-001",
                "speaker_id": "spk-engineer-a",
                "source_block_anchor": "block-robotics-overview",
                "text": "word " * 5000,
            }
        ],
        "renderer_text_limit": 240,
    }


@when("content intelligence prepares the episode")
def prepare_episode(context):
    context.values["renderer_probe"] = RendererProbe()
    try:
        service = import_module("poddown.content.service")
    except ModuleNotFoundError as error:
        raise AssertionError("poddown.content.service is not implemented") from error

    prepare_fn = getattr(service, "prepare_content", None)
    if prepare_fn is None:
        raise AssertionError("poddown.content.service.prepare_content is not implemented")

    context.values["result"] = _invoke_prepare_content(prepare_fn, context)


@when("the pronunciation is resolved")
def resolve_pronunciation(context):
    try:
        lexicon = import_module("poddown.content.lexicon")
    except ModuleNotFoundError as error:
        raise AssertionError("poddown.content.lexicon is not implemented") from error

    resolve_fn = getattr(lexicon, "resolve_pronunciation", None)
    if resolve_fn is None:
        raise AssertionError("poddown.content.lexicon.resolve_pronunciation is not implemented")

    context.values["result"] = resolve_fn(key="C1", layers=context.values["lexicon_layers"])


@when("the project pronunciation is resolved")
def resolve_project_pronunciation(context):
    try:
        lexicon = import_module("poddown.content.lexicon")
    except ModuleNotFoundError as error:
        raise AssertionError("poddown.content.lexicon is not implemented") from error

    resolve_fn = getattr(lexicon, "resolve_pronunciation", None)
    if resolve_fn is None:
        raise AssertionError("poddown.content.lexicon.resolve_pronunciation is not implemented")

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
        raise AssertionError("poddown.content.tokens.extract_critical_tokens is not implemented")

    context.values["result"] = extract_fn(context.values["critical_text"])


@when("the script is segmented")
def segment_script(context):
    try:
        segmentation = import_module("poddown.content.segmentation")
    except ModuleNotFoundError as error:
        raise AssertionError("poddown.content.segmentation is not implemented") from error

    segment_fn = getattr(segmentation, "segment_script", None)
    if segment_fn is None:
        raise AssertionError("poddown.content.segmentation.segment_script is not implemented")

    context.values["result"] = segment_fn(context.values["script"])


@then("the canonical script has two stable speakers and complete factual anchors")
def script_has_stable_speakers(context):
    result = context.values["result"]
    script = _attribute_or_key(result, "canonical_script", {})
    turns = _attribute_or_key(script, "turns", [])

    expected_turn_order = _expected_proposal(context)["expected_turn_order"]
    expected_source_blocks = _expected_proposal(context)["expected_source_blocks"]
    expected_anchor_ids = set(_expected_proposal(context)["factual_anchor_ids"])

    assert [turn["turn_id"] for turn in turns] == expected_turn_order
    assert [turn["source_block_anchor"] for turn in turns] == expected_source_blocks
    assert {
        "spk-archivist-1",
        "spk-controls-2",
    } == {turn["speaker_id"] for turn in turns}

    for claim_id in expected_anchor_ids:
        assert any(turn["claim_anchor"] == claim_id for turn in turns)


@then("the source snapshot frontmatter and hash are preserved")
def source_frontmatter_and_hash_is_preserved(context):
    result = context.values["result"]
    source_snapshot = _attribute_or_key(result, "source_snapshot", {})
    assert _attribute_or_key(source_snapshot, "source_sha256") == context.values["source_sha256"]

    frontmatter = _attribute_or_key(source_snapshot, "frontmatter", {})
    if isinstance(frontmatter, str):
        frontmatter = _extract_frontmatter(frontmatter)
    assert isinstance(frontmatter, dict)

    expected_frontmatter = context.values["source_frontmatter"]
    for key in ["profile", "episode_id", "duration_minutes", "target_date"]:
        assert _key_values_from(frontmatter, f"poddown.{key}") == _key_values_from(
            expected_frontmatter, f"poddown.{key}"
        )


@then("the script contains disagreement without unsupported claims")
def script_disagreement_only(context):
    result = context.values["result"]
    script = _attribute_or_key(result, "canonical_script", {})
    turns = _attribute_or_key(script, "turns", [])

    disagreement = [
        turn
        for turn in turns
        if turn["turn_id"] == _expected_proposal(context)["disagreement_turn_id"]
    ]
    assert disagreement
    assert disagreement[0]["supported_by_source"] is True
    assert disagreement[0]["is_disagreement"] is True


@then("every extracted critical token has an expected spoken form")
def critical_tokens_have_spoken_forms(context):
    tokens = _attribute_or_key(context.values["result"], "critical_tokens", [])
    expected = _expected_critical_tokens(context)

    assert len(tokens) == len(expected)

    observed = {
        _attribute_or_key(token, "surface")
        or _attribute_or_key(token, "token")
        or _attribute_or_key(token, "text"): token
        for token in tokens
    }

    for item in expected:
        surface = item["surface"]
        assert surface in observed
        token = observed[surface]
        assert token["spoken_form"] == item["spoken_form"]
        assert token["source_form"] == item["source_form"]
        assert token["category"] == item["category"]


@then("the segmentation manifest preserves turn order and source grouping")
def segmentation_manifest_preserves_structure(context):
    result = context.values["result"]
    manifest = _attribute_or_key(result, "segmentation_manifest", {})
    turn_ids = _attribute_or_key(manifest, "turn_ids", [])
    source_block_ids = _attribute_or_key(manifest, "source_block_ids", [])
    segments = _attribute_or_key(manifest, "segments", [])

    assert isinstance(turn_ids, list) and isinstance(source_block_ids, list)
    assert turn_ids == _expected_proposal(context)["expected_turn_order"]
    assert source_block_ids == _expected_proposal(context)["expected_source_blocks"]
    if segments:
        assert segments[0]["turn_id"] == _expected_proposal(context)["expected_turn_order"][0]


@then("adaptation fails with an unsupported claim error")
def adaptation_rejects_changed_number(context):
    result = context.values["result"]
    assert _attribute_or_key(result, "accepted") is False
    assert "unsupported claim" in str(_attribute_or_key(result, "error", "")).lower()


@then("no renderer call is made")
def no_renderer_call(context):
    probe = context.values["renderer_probe"]
    assert probe.call_count == 0
    assert _attribute_or_key(context.values["result"], "provider_calls", 0) == 0


@then("the episode layer wins and its version and entry ID are recorded")
def episode_layer_wins(context):
    result = context.values["result"]
    assert _attribute_or_key(result, "selected_layer") == "episode"
    assert _attribute_or_key(result, "selected_scope") == "episode"
    assert _attribute_or_key(result, "selected_spoken_form") == "see one episode"
    assert _attribute_or_key(result, "version") == "episode-v4"
    assert _attribute_or_key(result, "entry_id") == "c1-episode-v4"


@then("lexicon resolution fails closed with a conflict error")
def lexicon_conflict_closed(context):
    result = context.values["result"]
    assert _attribute_or_key(result, "accepted") is False
    assert "conflict" in str(_attribute_or_key(result, "error", "")).lower()
    assert _attribute_or_key(result, "selected_layer") is None
    assert _attribute_or_key(result, "selected_spoken_form") is None


@then("two distinct negation occurrences are present")
def negation_occurrences(context):
    tokens = _attribute_or_key(context.values["result"], "tokens", [])
    expected_negations = context.values["expected_negation_occurrences"]

    negations = [
        token
        for token in tokens
        if _attribute_or_key(token, "normalized") == "not"
        and _attribute_or_key(token, "category") == "negation"
    ]

    assert len(negations) == 2
    assert all(_attribute_or_key(negations[0], "occurrence_id") != _attribute_or_key(negations[1], "occurrence_id"))

    for expected in expected_negations:
        matches = [
            token
            for token in negations
            if _attribute_or_key(token, "span_start") == expected["span_start"]
            and _attribute_or_key(token, "span_end") == expected["span_end"]
            and _attribute_or_key(token, "occurrence_id") == expected["occurrence_id"]
        ]
        assert matches


@then("the token manifest is deterministic")
def deterministic_token_manifest(context):
    manifest = _attribute_or_key(context.values["result"], "manifest", {})
    assert _attribute_or_key(manifest, "deterministic") is True
    assert _attribute_or_key(manifest, "occurrence_count") == 2


@then("segmentation fails with a capability error")
def segmentation_capability_error(context):
    result = context.values["result"]
    assert _attribute_or_key(result, "accepted") is False
    assert "capability" in str(_attribute_or_key(result, "error", "")).lower()
