from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from pytest_bdd import given, scenarios, then, when

from poddown.content.source import snapshot_source
from poddown.content.tokens import extract_critical_tokens

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
            "evals": json.loads((FIXTURE / "evals.json").read_text(encoding="utf-8")),
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
    fixture = _load(context)
    context.values["tokens"] = extract_critical_tokens(fixture["article"])
    context.values["declared_tokens"] = fixture["profile"]["critical_tokens"]


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
    text = context.values["fixture"]["article"].lower()
    assert all(token.lower() in text for token in context.values["declared_tokens"])
    assert context.values["fixture"]["evals"]["critical_token_fidelity"] == 1.0


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
