from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import yaml
from uuid6 import uuid7

from poddown.audio import (
    DeterministicLocalRenderer,
    DurableRenderService,
    RenderRequest,
)
from poddown.audio import (
    VoiceConsent as AudioVoiceConsent,
)
from poddown.audio.storage import FilesystemArtifactStore, FilesystemRenderRecordStore
from poddown.content.adaptation import (
    AdaptationProposal,
    EpisodeTreatment,
    FixtureReasoningPort,
)
from poddown.content.lexicon import PronunciationEntry, PronunciationLexicon
from poddown.content.models import ScriptTurn, SourceAnchor, VoiceAsset, VoiceConsent
from poddown.content.segmentation import SegmentationCapabilities
from poddown.content.service import ContentPreparationRequest, prepare_content
from poddown.content.source import snapshot_source
from poddown.publishing import DisclosurePolicy, PublicationTarget
from poddown.qa.fidelity import evaluate_critical_tokens

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "integrations" / "signal-supply" / "v1"


def _fixture_request():
    article = (FIXTURE / "article.md").read_text(encoding="utf-8")
    profile_yaml = (FIXTURE / "show-profile.yaml").read_text(encoding="utf-8")
    proposal_data = json.loads(
        (FIXTURE / "adaptation.json").read_text(encoding="utf-8")
    )
    voices = yaml.safe_load((FIXTURE / "voices.yaml").read_text(encoding="utf-8"))
    lexicon_data = yaml.safe_load(
        (FIXTURE / "lexicons.yaml").read_text(encoding="utf-8")
    )
    snapshot = snapshot_source(article)

    def anchor(text: str) -> SourceAnchor:
        encoded = text.encode("utf-8")
        start = snapshot.source.encode("utf-8").index(encoded)
        end = start + len(encoded)
        block = next(
            block
            for block in snapshot.blocks
            if block.start <= start <= end <= block.end
        )
        return SourceAnchor(block.block_id, start, end)

    def block_anchor(text: str) -> SourceAnchor:
        needle = text.split(".", maxsplit=1)[0].encode("utf-8")
        start = snapshot.source.encode("utf-8").index(needle)
        block = next(
            block for block in snapshot.blocks if block.start <= start < block.end
        )
        return SourceAnchor(block.block_id, block.start, block.end)

    turn_rows = proposal_data["source_turns"]
    claim_rows = {row["claim_anchor"]: row for row in proposal_data["claims"]}
    turns = tuple(
        ScriptTurn(
            row["turn_id"],
            row["speaker_id"],
            claim_rows[row["claim_anchor"]]["adapted_value"],
            "factual",
            (block_anchor(claim_rows[row["claim_anchor"]]["source_value"]),),
            (block_anchor(claim_rows[row["claim_anchor"]]["source_value"]),),
        )
        for row in turn_rows
    )
    treatment = EpisodeTreatment(
        "signal-supply-semiconductor-v1",
        "dialogue",
        ("evidence", "challenge"),
        12,
        tuple(row["turn_id"] for row in turn_rows),
        {"spk-signal-host": "host", "spk-supply-analyst": "analyst"},
        tuple(
            block_anchor(claim_rows[row["claim_anchor"]]["source_value"])
            for row in turn_rows
        ),
        tuple(row["turn_id"] for row in turn_rows),
    )
    entries = tuple(
        PronunciationEntry(
            f"signal-supply-{index:02d}",
            row["key"],
            row["spoken_form"],
            "signal-supply-lexicon-v1",
            row["category"],
        )
        for index, row in enumerate(lexicon_data["entries"], start=1)
        if row["key"] in {"NVIDIA", "NVDA", "ASML", "TSMC", "7.5%", "3.2", "not"}
    )
    lexicon = PronunciationLexicon(
        "episode", "signal-supply-episode-lexicon-v1", entries
    )
    asset_ids = tuple(asset["asset_id"] for asset in voices["assets"])
    request = ContentPreparationRequest(
        markdown=article,
        profile_yaml=profile_yaml,
        treatment=treatment,
        reasoning=FixtureReasoningPort(
            {snapshot.source_sha256: AdaptationProposal(treatment, turns)}, {}
        ),
        lexicon_layers={"episode": lexicon},
        capabilities=SegmentationCapabilities(
            240, None, frozenset(row["speaker_id"] for row in turn_rows)
        ),
        voice_assets=tuple(VoiceAsset(asset_id, True) for asset_id in asset_ids),
        consents=tuple(VoiceConsent(asset_id, True) for asset_id in asset_ids),
    )
    return request, proposal_data, voices


def _prepared_fixture():
    request, proposal, voices = _fixture_request()
    prepared = prepare_content(request=request)
    return prepared, proposal, voices


def test_finance_article_uses_public_source_and_token_contracts_only():
    request, proposal, _ = _fixture_request()
    prepared = prepare_content(request=request)
    nvidia = next(token for token in prepared.tokens if token.source_form == "NVIDIA")
    assert nvidia.expected_spoken_form == "en-VID-ee-uh"
    assert (
        nvidia.pronunciation_source
        == "episode:signal-supply-episode-lexicon-v1:signal-supply-01"
    )
    assert {speaker.speaker_id for speaker in prepared.profile.speakers} == {
        "spk-signal-host",
        "spk-supply-analyst",
    }
    prepared_forms = {token.source_form for token in prepared.tokens}
    declared_forms = {
        item["source_form"] for item in proposal["expected_critical_tokens"]
    }
    assert declared_forms <= prepared_forms


def test_adapted_spoken_text_renders_locally_and_passes_public_fidelity_qa(tmp_path):
    prepared, _, voices = _prepared_fixture()
    transcript = (FIXTURE / "spoken-transcript.txt").read_text(encoding="utf-8")
    tokens = tuple(token.expected_spoken_form for token in prepared.tokens)
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    renderer = DeterministicLocalRenderer()
    service = DurableRenderService(
        artifacts, FilesystemRenderRecordStore(tmp_path / "records", artifacts)
    )
    outcomes = []
    for segment in prepared.segments:
        speaker = next(
            item
            for item in prepared.profile.speakers
            if item.speaker_id == segment.speaker_ids[0]
        )
        request = RenderRequest(
            episode_id="signal-supply-demo",
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
            evidence_id=next(
                item["consent_id"]
                for item in voices["assets"]
                if item["asset_id"] == speaker.voice_asset_id
            ),
            allowed_providers=frozenset({"local"}),
        )
        outcomes.extend(asyncio.run(service.render_takes(request, consent, renderer)))
    fidelity = evaluate_critical_tokens(tokens, transcript)

    assert len(outcomes) == len(prepared.segments)
    assert {outcome.candidate.provider for outcome in outcomes} == {"local"}
    assert {outcome.candidate.voice_asset_id for outcome in outcomes} == {
        item.voice_asset_id for item in prepared.profile.speakers
    }
    assert len(renderer.calls) == len(prepared.segments)
    assert fidelity.passed is True
    assert fidelity.accuracy == 1.0


def test_robotics_fixture_digest_is_regression_baseline():
    robotics = ROOT / "tests" / "fixtures" / "content" / "robotics-mapping.md"
    source = robotics.read_bytes()
    assert (
        hashlib.sha256(source).hexdigest()
        == "ebe2aa14610aafad0fdca688ec156b321a7ae15d5bd98752a9b9f75e318b5c1e"
    )


def test_publication_target_and_disclosure_use_existing_contracts():
    target = yaml.safe_load(
        (FIXTURE / "publishing-target.yaml").read_text(encoding="utf-8")
    )
    disclosure = yaml.safe_load(
        (FIXTURE / "disclosure.yaml").read_text(encoding="utf-8")
    )
    publication_target = PublicationTarget(
        tenant_id=uuid7(),
        project_id=uuid7(),
        target_id=target["target_id"],
        kind=target["kind"],
        secret_ref=target["secret_ref"],
        show_id=target["show_id"],
        feed_url=target["feed_url"],
        disclosure=DisclosurePolicy(
            spoken=disclosure["spoken"],
            show_notes=disclosure["show_notes"],
            platform=disclosure["platform"],
        ),
    )
    assert publication_target.disclosure.spoken is True
    assert publication_target.secret_ref.startswith("secret://")
