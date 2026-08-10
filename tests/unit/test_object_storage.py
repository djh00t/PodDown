"""Pure contract tests for tenant-scoped object references and keys."""

from __future__ import annotations

from uuid import UUID

import pytest
from poddown.object_storage import (
    ObjectRef,
    ObjectValidationError,
    storage_key_for,
    validate_sha256,
)

TENANT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")
DIGEST = "a" * 64


def test_storage_key_is_tenant_project_scoped_and_content_addressed() -> None:
    key = storage_key_for(TENANT_ID, PROJECT_ID, DIGEST)

    assert key == (
        "tenants/018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10/"
        "projects/018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12/objects/aa/" + DIGEST
    )


@pytest.mark.parametrize(
    ("name", "media_type"),
    [("../escape", "text/markdown"), ("fixture.md", "text markdown")],
)
def test_object_ref_rejects_path_or_media_metadata(name: str, media_type: str) -> None:
    with pytest.raises(ObjectValidationError):
        ObjectRef(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            name=name,
            media_type=media_type,
            byte_count=1,
            sha256=DIGEST,
            storage_key=storage_key_for(TENANT_ID, PROJECT_ID, DIGEST),
        )


def test_object_ref_rejects_noncanonical_key_and_checksum() -> None:
    with pytest.raises(ObjectValidationError):
        validate_sha256("not-a-digest")

    with pytest.raises(ObjectValidationError):
        ObjectRef(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            name="fixture.md",
            media_type="text/markdown",
            byte_count=1,
            sha256=DIGEST,
            storage_key="objects/aa/" + DIGEST,
        )
