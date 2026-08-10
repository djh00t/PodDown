from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from uuid6 import uuid7

from poddown.content.service import prepare_content
from poddown.content.source import snapshot_source
from poddown.content.tokens import extract_critical_tokens
from poddown.publishing import DisclosurePolicy, PublicationTarget

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
