"""BDD bindings for tenant-scoped immutable object storage."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from poddown.object_storage import (
    FilesystemObjectStore,
    ObjectIntegrityError,
    ObjectScopeError,
)
from pytest_bdd import given, scenarios, then, when

TENANT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")
OTHER_PROJECT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b13")
DATA = b"# tenant object fixture\n"

scenarios("../features/tenant_object_storage.feature")


@pytest.fixture
def object_context(tmp_path: Path) -> dict[str, object]:
    return {"store": FilesystemObjectStore(tmp_path)}


@given("an empty tenant object store")
def empty_store(object_context: dict[str, object]) -> None:
    assert isinstance(object_context["store"], FilesystemObjectStore)


@given("an object stored in the tenant object store")
def stored_object(object_context: dict[str, object]) -> None:
    store = object_context["store"]
    assert isinstance(store, FilesystemObjectStore)
    object_context["reference"] = store.put(
        TENANT_ID,
        PROJECT_ID,
        name="fixture.md",
        media_type="text/markdown",
        data=DATA,
    )


@when("I put the same Markdown bytes twice")
def replay_put(object_context: dict[str, object]) -> None:
    store = object_context["store"]
    assert isinstance(store, FilesystemObjectStore)
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
    object_context["first"] = first
    object_context["second"] = second


@then("both references and reads are identical")
def replay_result(object_context: dict[str, object]) -> None:
    store = object_context["store"]
    assert isinstance(store, FilesystemObjectStore)
    assert object_context["first"] == object_context["second"]
    assert store.read(TENANT_ID, PROJECT_ID, object_context["first"]) == DATA


@when("I put identical bytes for two project scopes")
def distinct_scopes(object_context: dict[str, object]) -> None:
    store = object_context["store"]
    assert isinstance(store, FilesystemObjectStore)
    object_context["first"] = store.put(
        TENANT_ID,
        PROJECT_ID,
        name="fixture.md",
        media_type="text/markdown",
        data=DATA,
    )
    object_context["second"] = store.put(
        TENANT_ID,
        OTHER_PROJECT_ID,
        name="fixture.md",
        media_type="text/markdown",
        data=DATA,
    )


@then("the canonical keys are distinct and cross-scope reads fail")
def distinct_scope_result(object_context: dict[str, object]) -> None:
    store = object_context["store"]
    assert isinstance(store, FilesystemObjectStore)
    first = object_context["first"]
    second = object_context["second"]
    assert first.storage_key != second.storage_key
    with pytest.raises(ObjectScopeError):
        store.read(TENANT_ID, OTHER_PROJECT_ID, first)


@when("the stored bytes are corrupted")
def corrupt_object(object_context: dict[str, object]) -> None:
    store = object_context["store"]
    reference = object_context["reference"]
    assert isinstance(store, FilesystemObjectStore)
    path = store._root / reference.storage_key  # noqa: SLF001
    path.write_bytes(b"corrupt")


@then("the object read fails with an integrity error")
def corruption_result(object_context: dict[str, object]) -> None:
    store = object_context["store"]
    reference = object_context["reference"]
    assert isinstance(store, FilesystemObjectStore)
    with pytest.raises(ObjectIntegrityError):
        store.read(TENANT_ID, PROJECT_ID, reference)
