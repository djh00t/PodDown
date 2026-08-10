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
    VoiceConsent,
)
from poddown.audio.storage import FilesystemArtifactStore, FilesystemRenderRecordStore
from poddown.content.service import prepare_content
from poddown.content.source import snapshot_source
from poddown.content.tokens import extract_critical_tokens
from poddown.publishing import DisclosurePolicy, PublicationTarget
from poddown.qa.fidelity import evaluate_critical_tokens

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "integrations" / "signal-supply" / "v1"


def test_finance_article_uses_public_source_and_token_contracts_only():
    article = (FIXTURE / "article.md").read_text(encoding="utf-8")
    profile = (FIXTURE / "show-profile.yaml").read_text(encoding="utf-8")
    proposal = json.loads((FIXTURE / "adaptation.json").read_text(encoding="utf-8"))
    snapshot = snapshot_source(article)
    tokens = extract_critical_tokens(article)
    prepared = prepare_content(
        source=article, profile=profile, proposal=proposal, renderer=None
    )
    assert snapshot.source == article
    assert snapshot.source_sha256 == hashlib.sha256(article.encode()).hexdigest()
    assert tokens
    assert prepared.accepted is True
    assert not any("provider" in token.category.lower() for token in tokens)


def test_adapted_spoken_text_renders_locally_and_passes_public_fidelity_qa(tmp_path):
    article = (FIXTURE / "article.md").read_text(encoding="utf-8")
    proposal = json.loads((FIXTURE / "adaptation.json").read_text(encoding="utf-8"))
    prepared = prepare_content(
        source=article,
        profile=(FIXTURE / "show-profile.yaml").read_text(encoding="utf-8"),
        proposal=proposal,
        renderer=None,
    )
    spoken_text = (FIXTURE / "spoken-transcript.txt").read_text(encoding="utf-8")
    tokens = tuple(item["spoken_form"] for item in proposal["expected_critical_tokens"])
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    renderer = DeterministicLocalRenderer()
    service = DurableRenderService(
        artifacts, FilesystemRenderRecordStore(tmp_path / "records", artifacts)
    )
    request = RenderRequest(
        episode_id="signal-supply-demo",
        episode_version="v1",
        segment_id="article",
        speaker_id="synthetic-presenter",
        expected_spoken_text=spoken_text,
        voice_asset_id="demo-voice-signal-supply-v1",
        provider="local",
        model="local-deterministic-v1",
    )
    consent = VoiceConsent(
        voice_asset_id=request.voice_asset_id,
        evidence_id="synthetic-demo-consent-v1",
        allowed_providers=frozenset({"local"}),
    )

    outcomes = asyncio.run(service.render_takes(request, consent, renderer))
    fidelity = evaluate_critical_tokens(tokens, request.expected_spoken_text)

    assert prepared.accepted is True
    assert len(outcomes) == 1
    assert renderer.calls == [request.idempotency_key]
    assert outcomes[0].candidate.provider == "local"
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
