"""BDD bindings for tenant-scoped immutable object storage."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pytest_bdd import given, scenarios, then, when

from poddown.object_storage import (
    FilesystemObjectStore,
    ObjectIntegrityError,
    ObjectNotFound,
    ObjectRef,
    ObjectScopeError,
    ObjectValidationError,
)

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


@when("an object-store ancestor is replaced by a symlink")
def replace_ancestor_with_symlink(object_context: dict[str, object]) -> None:
    store = object_context["store"]
    reference = object_context["reference"]
    assert isinstance(store, FilesystemObjectStore)
    real_tenants = store._root / "tenants-real"  # noqa: SLF001
    (store._root / "tenants").rename(real_tenants)  # noqa: SLF001
    (store._root / "tenants").symlink_to(real_tenants, target_is_directory=True)  # noqa: SLF001
    object_context["reference"] = reference


@when("I read it with changed display metadata")
def forge_reference_metadata(object_context: dict[str, object]) -> None:
    reference = object_context["reference"]
    object_context["reference"] = replace(reference, name="forged.md")


@when("the stored object is removed")
def remove_stored_object(object_context: dict[str, object]) -> None:
    store = object_context["store"]
    reference = object_context["reference"]
    assert isinstance(store, FilesystemObjectStore)
    (store._root / reference.storage_key).unlink()  # noqa: SLF001


@then("the object read fails with a missing-object error")
def missing_object_result(object_context: dict[str, object]) -> None:
    store = object_context["store"]
    reference = object_context["reference"]
    assert isinstance(store, FilesystemObjectStore)
    with pytest.raises(ObjectNotFound):
        store.read(TENANT_ID, PROJECT_ID, reference)


@when("I replay its put after removing the metadata sidecar")
def replay_after_metadata_removal(object_context: dict[str, object]) -> None:
    store = object_context["store"]
    reference = object_context["reference"]
    assert isinstance(store, FilesystemObjectStore)
    metadata_path = store._root / reference.storage_key  # noqa: SLF001
    metadata_path.with_name(f".{reference.sha256}.metadata.json").unlink()
    object_context["replay"] = store.put(
        TENANT_ID,
        PROJECT_ID,
        name="fixture.md",
        media_type="text/markdown",
        data=DATA,
    )


@then("the replay recovers the exact metadata sidecar")
def metadata_sidecar_recovered(object_context: dict[str, object]) -> None:
    store = object_context["store"]
    reference = object_context["reference"]
    replay = object_context["replay"]
    assert isinstance(store, FilesystemObjectStore)
    assert replay == reference
    assert store.read(TENANT_ID, PROJECT_ID, replay) == DATA


@when("I submit a UUID4 scope and path-traversal name")
def submit_malformed_input(object_context: dict[str, object]) -> None:
    store = object_context["store"]
    assert isinstance(store, FilesystemObjectStore)
    with pytest.raises(ObjectValidationError):
        store.put(
            uuid4(),
            PROJECT_ID,
            name="fixture.md",
            media_type="text/markdown",
            data=DATA,
        )
    with pytest.raises(ObjectValidationError):
        store.put(
            TENANT_ID,
            PROJECT_ID,
            name="../escape",
            media_type="text/markdown",
            data=DATA,
        )
    object_context["malformed_input_checked"] = True


@then("both puts fail with validation errors")
def malformed_input_result(object_context: dict[str, object]) -> None:
    assert object_context["malformed_input_checked"] is True


@when("I construct a malformed checksum reference")
def construct_malformed_reference(object_context: dict[str, object]) -> None:
    with pytest.raises(ObjectValidationError):
        ObjectRef(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            name="fixture.md",
            media_type="text/markdown",
            byte_count=0,
            sha256="not-a-checksum",
            storage_key="not-a-canonical-key",
        )
    object_context["malformed_reference_checked"] = True


@then("reference construction fails validation")
def malformed_reference_result(object_context: dict[str, object]) -> None:
    assert object_context["malformed_reference_checked"] is True


@then("the object store is local and provider-free")
def local_provider_free_result(object_context: dict[str, object]) -> None:
    store = object_context["store"]
    assert isinstance(store, FilesystemObjectStore)
    assert type(store).__module__ == "poddown.object_storage"
