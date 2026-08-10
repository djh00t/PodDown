"""Filesystem integration tests for tenant-scoped immutable objects."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from poddown.object_storage import (
    FilesystemObjectStore,
    ObjectIntegrityError,
    ObjectNotFound,
    ObjectScopeError,
    ObjectValidationError,
)

TENANT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
OTHER_TENANT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11")
PROJECT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")
OTHER_PROJECT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b13")
DATA = b"---\npoddown:\n  profile: technical-dialogue\n---\n# fixture\n"


def test_put_replays_exact_reference_and_read_bytes(tmp_path: Path) -> None:
    store = FilesystemObjectStore(tmp_path)

    first = store.put(
        TENANT_ID,
        PROJECT_ID,
        name="fixture.md",
        media_type="text/markdown",
        data=DATA,
    )
    second = store.put(
        TENANT_ID,
        PROJECT_ID,
        name="fixture.md",
        media_type="text/markdown",
        data=DATA,
    )

    assert second == first
    assert store.read(TENANT_ID, PROJECT_ID, first) == DATA
    assert (tmp_path / first.storage_key).read_bytes() == DATA


def test_identical_bytes_have_distinct_keys_for_distinct_scopes(tmp_path: Path) -> None:
    store = FilesystemObjectStore(tmp_path)
    first = store.put(
        TENANT_ID,
        PROJECT_ID,
        name="fixture.md",
        media_type="text/markdown",
        data=DATA,
    )
    second = store.put(
        OTHER_TENANT_ID,
        OTHER_PROJECT_ID,
        name="fixture.md",
        media_type="text/markdown",
        data=DATA,
    )

    assert first.storage_key != second.storage_key
    with pytest.raises(ObjectScopeError):
        store.read(OTHER_TENANT_ID, OTHER_PROJECT_ID, first)


def test_corruption_and_missing_objects_fail_closed(tmp_path: Path) -> None:
    store = FilesystemObjectStore(tmp_path)
    reference = store.put(
        TENANT_ID,
        PROJECT_ID,
        name="fixture.md",
        media_type="text/markdown",
        data=DATA,
    )
    path = tmp_path / reference.storage_key
    path.write_bytes(b"corrupted")
    with pytest.raises(ObjectIntegrityError):
        store.read(TENANT_ID, PROJECT_ID, reference)

    path.unlink()
    with pytest.raises(ObjectNotFound):
        store.read(TENANT_ID, PROJECT_ID, reference)


def test_put_rejects_invalid_metadata_before_filesystem_write(tmp_path: Path) -> None:
    store = FilesystemObjectStore(tmp_path)

    with pytest.raises(ObjectValidationError):
        store.put(
            TENANT_ID,
            PROJECT_ID,
            name="../escape",
            media_type="text/markdown",
            data=DATA,
        )
