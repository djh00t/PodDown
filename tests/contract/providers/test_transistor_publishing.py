import json
from pathlib import Path
from uuid import UUID

import pytest

from poddown.publishing import (
    DisclosurePolicy,
    PublicationAuthorization,
    PublicationReceipt,
    PublishingValidationError,
    RecordedTransistorAdapter,
)


def test_recorded_transistor_create_fixture_has_stable_external_id() -> None:
    fixture = json.loads(
        (
            Path(__file__).parents[2]
            / "fixtures/providers/transistor/episode-create.json"
        ).read_text()
    )
    assert RecordedTransistorAdapter(fixture).fixture["id"] == "transistor-episode-1"


def test_recorded_transistor_update_fixture_is_offline_data() -> None:
    fixture = json.loads(
        (
            Path(__file__).parents[2]
            / "fixtures/providers/transistor/episode-update.json"
        ).read_text()
    )
    assert fixture["operation"] == "update"


def test_recorded_transistor_mutations_require_matching_success_fixtures() -> None:
    receipt = PublicationReceipt(
        "publication-1",
        UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10"),
        UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12"),
        "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11",
        "target",
        "key",
        "a" * 64,
        "transistor-episode-1",
        "published",
        PublicationAuthorization("a", "d", "r"),
        DisclosurePolicy(),
    )
    adapter = RecordedTransistorAdapter(
        {"id": "transistor-episode-1", "operation": "update", "status": "updated"}
    )
    assert adapter.update(receipt) == "transistor-updated:transistor-episode-1"
    with pytest.raises(PublishingValidationError):
        RecordedTransistorAdapter(
            {"id": "different", "operation": "delete", "status": "failed"}
        ).delete(receipt)
