"""Filesystem integration tests for tenant-scoped immutable objects."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from threading import Barrier, Lock
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


def test_symlinked_object_path_fails_closed(tmp_path: Path) -> None:
    store = FilesystemObjectStore(tmp_path)
    reference = store.put(
        TENANT_ID,
        PROJECT_ID,
        name="fixture.md",
        media_type="text/markdown",
        data=DATA,
    )
    path = tmp_path / reference.storage_key
    path.unlink()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(DATA)
    path.symlink_to(outside)

    with pytest.raises(ObjectIntegrityError):
        store.read(TENANT_ID, PROJECT_ID, reference)


def test_symlinked_parent_directory_fails_closed(tmp_path: Path) -> None:
    store = FilesystemObjectStore(tmp_path)
    reference = store.put(
        TENANT_ID,
        PROJECT_ID,
        name="fixture.md",
        media_type="text/markdown",
        data=DATA,
    )
    real_tenants = tmp_path / "tenants-real"
    (tmp_path / "tenants").rename(real_tenants)
    (tmp_path / "tenants").symlink_to(real_tenants, target_is_directory=True)

    with pytest.raises(ObjectIntegrityError):
        store.read(TENANT_ID, PROJECT_ID, reference)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "different-name.md"),
        ("media_type", "application/octet-stream"),
        ("byte_count", len(DATA) + 1),
    ],
)
def test_read_requires_exact_persisted_reference_metadata(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    store = FilesystemObjectStore(tmp_path)
    reference = store.put(
        TENANT_ID,
        PROJECT_ID,
        name="fixture.md",
        media_type="text/markdown",
        data=DATA,
    )
    forged_reference = replace(reference, **{field: value})

    with pytest.raises(ObjectIntegrityError):
        store.read(TENANT_ID, PROJECT_ID, forged_reference)


def test_same_digest_metadata_conflict_fails_closed(tmp_path: Path) -> None:
    store = FilesystemObjectStore(tmp_path)
    store.put(
        TENANT_ID,
        PROJECT_ID,
        name="fixture.md",
        media_type="text/markdown",
        data=DATA,
    )

    with pytest.raises(ObjectIntegrityError):
        store.put(
            TENANT_ID,
            PROJECT_ID,
            name="renamed.md",
            media_type="text/markdown",
            data=DATA,
        )


def test_missing_persisted_metadata_fails_closed(tmp_path: Path) -> None:
    store = FilesystemObjectStore(tmp_path)
    reference = store.put(
        TENANT_ID,
        PROJECT_ID,
        name="fixture.md",
        media_type="text/markdown",
        data=DATA,
    )
    metadata_path = tmp_path / reference.storage_key
    metadata_path = metadata_path.with_name(f".{reference.sha256}.metadata.json")
    metadata_path.unlink()

    with pytest.raises(ObjectIntegrityError):
        store.read(TENANT_ID, PROJECT_ID, reference)


def test_metadata_create_race_is_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FilesystemObjectStore(tmp_path)
    real_create_once = FilesystemObjectStore._create_once

    def create_conflicting_metadata(
        parent_fd: int,
        name: str,
        data: bytes,
        digest: str,
        byte_count: int,
    ) -> bool:
        if name.endswith(".metadata.json"):
            conflicting = b'{"conflict":true}'
            real_create_once(
                parent_fd,
                name,
                conflicting,
                sha256(conflicting).hexdigest(),
                len(conflicting),
            )
            return False
        return real_create_once(parent_fd, name, data, digest, byte_count)

    monkeypatch.setattr(
        FilesystemObjectStore,
        "_create_once",
        staticmethod(create_conflicting_metadata),
    )

    with pytest.raises(ObjectIntegrityError):
        store.put(
            TENANT_ID,
            PROJECT_ID,
            name="fixture.md",
            media_type="text/markdown",
            data=DATA,
        )


def test_concurrent_same_digest_puts_never_return_forged_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FilesystemObjectStore(tmp_path)
    initial_object_misses = 0
    miss_lock = Lock()
    object_open_barrier = Barrier(2)
    real_open_file = FilesystemObjectStore._open_file

    def force_both_writers_into_create(parent_fd: int, name: str) -> int:
        nonlocal initial_object_misses
        force_missing = False
        with miss_lock:
            if len(name) == 64 and initial_object_misses < 2:
                initial_object_misses += 1
                force_missing = True
        if force_missing:
            object_open_barrier.wait(timeout=5)
            raise ObjectNotFound("forced concurrent create miss")
        return real_open_file(parent_fd, name)

    monkeypatch.setattr(
        FilesystemObjectStore,
        "_open_file",
        staticmethod(force_both_writers_into_create),
    )

    def put(name: str) -> tuple[str, object]:
        try:
            return (
                "success",
                store.put(
                    TENANT_ID,
                    PROJECT_ID,
                    name=name,
                    media_type="text/markdown",
                    data=DATA,
                ),
            )
        except ObjectIntegrityError as error:
            return ("failure", error)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(put, ("first.md", "second.md")))

    successes = [value for status, value in results if status == "success"]
    failures = [value for status, value in results if status == "failure"]
    assert len(successes) == 1
    assert len(failures) == 1
    assert store.read(TENANT_ID, PROJECT_ID, successes[0]) == DATA


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


def test_read_rejects_non_reference_input(tmp_path: Path) -> None:
    store = FilesystemObjectStore(tmp_path)

    with pytest.raises(ObjectValidationError):
        store.read(TENANT_ID, PROJECT_ID, object())  # type: ignore[arg-type]
