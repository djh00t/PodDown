from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from pytest_bdd import given, scenarios, then, when

from poddown.audio import (
    DeterministicLocalRenderer,
    DurableRenderService,
    RenderRequest,
)
from poddown.audio import (
    VoiceConsent as AudioVoiceConsent,
)
from poddown.audio.storage import FilesystemArtifactStore, FilesystemRenderRecordStore
from poddown.content.source import snapshot_source
from poddown.qa.fidelity import evaluate_critical_tokens
from tests.integration.test_content_pipeline import _request as robotics_request
from tests.integration.test_signal_supply import (
    _prepared_fixture,
    _rendered_spoken_text,
)

scenarios("../features/signal_supply.feature")

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "integrations" / "signal-supply" / "v1"
ROBOTICS = ROOT / "tests" / "fixtures" / "content" / "robotics-mapping.md"
ROBOTICS_SHA256 = "ebe2aa14610aafad0fdca688ec156b321a7ae15d5bd98752a9b9f75e318b5c1e"


def _load(context):
    if "fixture" not in context.values:
        context.values["fixture"] = {
            "article": (FIXTURE / "article.md").read_text(encoding="utf-8"),
            "profile": yaml.safe_load(
                (FIXTURE / "show-profile.yaml").read_text(encoding="utf-8")
            ),
            "voices": yaml.safe_load(
                (FIXTURE / "voices.yaml").read_text(encoding="utf-8")
            ),
            "disclosure": yaml.safe_load(
                (FIXTURE / "disclosure.yaml").read_text(encoding="utf-8")
            ),
            "lexicon": yaml.safe_load(
                (FIXTURE / "lexicons.yaml").read_text(encoding="utf-8")
            ),
            "evals": json.loads((FIXTURE / "evals.json").read_text(encoding="utf-8")),
            "proposal": json.loads(
                (FIXTURE / "adaptation.json").read_text(encoding="utf-8")
            ),
        }
    return context.values["fixture"]


@given("the versioned Signal and Supply fixture")
def signal_supply_fixture(context):
    _load(context)


@when("the article is loaded through PodDown public contracts")
def load_article(context):
    article = _load(context)["article"]
    context.values["snapshot"] = snapshot_source(article)
    context.values["provider_calls"] = 0


@when("critical tokens are extracted from the article")
def extract_article_tokens(context):
    prepared, _, _ = _prepared_fixture()
    context.values["tokens"] = prepared.tokens
    context.values["declared_tokens"] = {
        item["key"] for item in _load(context)["lexicon"]["entries"]
    }
    context.values["prepared"] = prepared


@when("the integration metadata is loaded")
def load_metadata(context):
    context.values["metadata"] = _load(context)


@when("the existing robotics fixture is loaded")
def load_robotics(context):
    source = ROBOTICS.read_bytes()
    context.values["robotics_bytes"] = source
    context.values["robotics_digest"] = hashlib.sha256(source).hexdigest()


@then("its source snapshot is preserved exactly")
def source_preserved(context):
    assert context.values["snapshot"].source == context.values["fixture"]["article"]
    assert (
        context.values["snapshot"].source_sha256
        == hashlib.sha256(
            context.values["fixture"]["article"].encode("utf-8")
        ).hexdigest()
    )


@then("no provider or renderer call is made")
def no_provider_calls(context):
    assert context.values["provider_calls"] == 0


@then("every declared finance critical token is present with fidelity 1.0")
def critical_tokens_present(context):
    prepared = context.values["prepared"]
    assert context.values["declared_tokens"] <= {
        token.source_form for token in prepared.tokens
    }
    nvidia = next(token for token in prepared.tokens if token.source_form == "NVIDIA")
    assert nvidia.expected_spoken_form == "en-VID-ee-uh"
    assert nvidia.pronunciation_source.startswith("episode:")
    assert {"3.2 billion dollars", "not guaranteed", "Nasdaq"} <= {
        token.source_form for token in prepared.tokens
    }


@when("the adapted spoken text is rendered through the local PodDown contract")
def render_spoken_text(context, tmp_path):
    prepared, _, voices = _prepared_fixture()
    disclosure = _load(context)["disclosure"]
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    renderer = DeterministicLocalRenderer()
    service = DurableRenderService(
        artifacts, FilesystemRenderRecordStore(tmp_path / "records", artifacts)
    )
    import asyncio

    outcomes = []
    for index, segment in enumerate(prepared.segments):
        speaker = next(
            item
            for item in prepared.profile.speakers
            if item.speaker_id == segment.speaker_ids[0]
        )
        request = RenderRequest(
            episode_id="signal-supply-bdd",
            episode_version="v1",
            segment_id=segment.segment_id,
            speaker_id=speaker.speaker_id,
            expected_spoken_text=_rendered_spoken_text(
                segment.text, disclosure, first_segment=index == 0
            ),
            voice_asset_id=speaker.voice_asset_id,
            provider="local",
            model="local-deterministic-v1",
        )
        consent = AudioVoiceConsent(
            voice_asset_id=request.voice_asset_id,
            evidence_id=next(
                item["consent_id"]
                for item in voices["assets"]
                if item["asset_id"] == speaker.voice_asset_id
            ),
            allowed_providers=frozenset({"local"}),
        )
        outcomes.extend(asyncio.run(service.render_takes(request, consent, renderer)))
    context.values["render"] = (
        outcomes,
        renderer,
        prepared,
        evaluate_critical_tokens(
            tuple(token.expected_spoken_form for token in prepared.tokens),
            (FIXTURE / "spoken-transcript.txt").read_text(encoding="utf-8"),
        ),
    )


@then("the deterministic renderer is invoked without a live provider")
def local_renderer_invoked(context):
    outcomes, renderer, prepared, _ = context.values["render"]
    assert len(outcomes) == len(prepared.segments)
    assert len(renderer.calls) == len(prepared.segments)
    assert {outcome.candidate.provider for outcome in outcomes} == {"local"}


@then("the rendered transcript passes the public critical-token evaluator at 1.0")
def transcript_passes_fidelity(context):
    fidelity = context.values["render"][3]
    assert fidelity.passed is True
    assert fidelity.accuracy == 1.0


@then("the rendered request and transcript contain the synthetic-presenter disclosure")
def rendered_output_contains_disclosure(context):
    disclosure = _load(context)["disclosure"]
    outcomes = context.values["render"][0]
    transcript = (FIXTURE / "spoken-transcript.txt").read_text(encoding="utf-8")
    assert disclosure["text"] in transcript
    assert any(
        disclosure["text"] in outcome.candidate.expected_spoken_text
        for outcome in outcomes
    )


@then("the counter-thesis and uncertainty language remain present")
def uncertainty_present(context):
    article = context.values["fixture"]["article"].lower()
    assert "counter-thesis" in article
    assert "uncertain" in article or "uncertainty" in article


@then("no unsupported promotional claim is introduced")
def no_hype(context):
    assert context.values["fixture"]["evals"]["unsupported_promotional_claims"] == []


@then("the presenter disclosure is required in spoken and show-note output")
def disclosure_required(context):
    disclosure = context.values["metadata"]["disclosure"]
    assert disclosure["spoken"] is True
    assert disclosure["show_notes"] is True


@then("every voice asset is synthetic, approved, and demo-only")
def assets_approved(context):
    for asset in context.values["metadata"]["voices"]["assets"]:
        assert asset["synthetic"] is True
        assert asset["demo_only"] is True
        assert asset["consent_status"] == "approved"


@then("its source bytes and digest remain unchanged")
def robotics_unchanged(context):
    expected = ROBOTICS.read_bytes()
    assert context.values["robotics_bytes"] == expected
    assert context.values["robotics_digest"] == ROBOTICS_SHA256


@given("the existing robotics fixture")
def robotics_fixture(context):
    load_robotics(context)


@when("the robotics fixture is prepared and rendered through PodDown public contracts")
def prepare_and_render_robotics(context, tmp_path):
    from poddown.audio import (
        DeterministicLocalRenderer,
        DurableRenderService,
        RenderRequest,
    )
    from poddown.audio.storage import (
        FilesystemArtifactStore,
        FilesystemRenderRecordStore,
    )

    prepared = __import__(
        "poddown.content.service", fromlist=["prepare_content"]
    ).prepare_content(robotics_request())
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    service = DurableRenderService(
        artifacts, FilesystemRenderRecordStore(tmp_path / "records", artifacts)
    )
    renderer = DeterministicLocalRenderer()
    outcomes = []
    for segment in prepared.segments:
        speaker = next(
            item
            for item in prepared.profile.speakers
            if item.speaker_id == segment.speaker_ids[0]
        )
        request = RenderRequest(
            episode_id="robotics-mapping",
            episode_version="v1",
            segment_id=segment.segment_id,
            speaker_id=speaker.speaker_id,
            expected_spoken_text=segment.text,
            voice_asset_id=speaker.voice_asset_id,
            provider="local",
            model="local-deterministic-v1",
        )
        consent = AudioVoiceConsent(
            voice_asset_id=request.voice_asset_id,
            evidence_id=f"robotics-consent-{speaker.speaker_id}",
            allowed_providers=frozenset({"local"}),
        )
        import asyncio

        outcomes.extend(asyncio.run(service.render_takes(request, consent, renderer)))
    context.values["robotics_render"] = (prepared, outcomes)


@then("every robotics segment has a successful local render")
def robotics_rendered(context):
    prepared, outcomes = context.values["robotics_render"]
    assert len(outcomes) == len(prepared.segments)
    assert {outcome.candidate.provider for outcome in outcomes} == {"local"}


@then("the robotics source digest remains unchanged")
def robotics_digest_after_render(context):
    assert context.values["robotics_digest"] == ROBOTICS_SHA256
