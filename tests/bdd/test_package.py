"""Executable acceptance tests for immutable episode package commits."""

import json
from hashlib import sha256
from importlib import import_module
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

scenarios("../features/package.feature")

EPISODE_VERSION_ID = "123e4567-e89b-12d3-a456-426614174000"
APPROVED_NAMES = (
    "episode.wav",
    "episode.mp3",
    "transcript.txt",
    "transcript.vtt",
    "chapters.json",
    "show-notes.md",
    "qa-report.json",
    "provenance.json",
    "render-manifest.json",
)
MEDIA_TYPES = {
    "episode.wav": "audio/wav",
    "episode.mp3": "audio/mpeg",
    "transcript.txt": "text/plain",
    "transcript.vtt": "text/vtt",
    "chapters.json": "application/json",
    "show-notes.md": "text/markdown",
    "qa-report.json": "application/json",
    "provenance.json": "application/json",
    "render-manifest.json": "application/json",
}
TOP_LEVEL_SCHEMA_FIELDS = {
    "schema_version",
    "episode_version_id",
    "files",
    "provenance",
}
FILE_SCHEMA_FIELDS = {"name", "media_type", "bytes", "sha256"}


def _package_module():
    try:
        return import_module("poddown.packages")
    except ModuleNotFoundError as error:
        raise AssertionError("Immutable package assembly is not implemented") from error


def _artifact_module():
    try:
        return import_module("poddown.artifacts")
    except ModuleNotFoundError as error:
        raise AssertionError("Immutable artifact storage is not implemented") from error


def _artifact_data(name: str) -> bytes:
    return f"immutable fixture for {name}\n".encode()


def _provenance(*, qa: str = "pass", accuracy: float = 1.0, wav: bytes):
    packages = _package_module()
    return packages.PackageProvenance(
        source_sha256="a" * 64,
        script_version=1,
        profile_version="spoken-word-v1",
        renderer="local-deterministic-v1",
        qa=qa,
        critical_token_accuracy=accuracy,
        final_sha256=sha256(wav).hexdigest(),
        details={"fixture": "immutable-package"},
    )


def _artifacts(*, omitted: str | None = None, unsafe_name: str | None = None):
    packages = _package_module()
    artifacts = []
    for name in APPROVED_NAMES:
        if name == omitted:
            continue
        artifact_name = unsafe_name if name == "episode.wav" and unsafe_name else name
        artifacts.append(
            packages.PackageArtifact(
                name=artifact_name,
                media_type=MEDIA_TYPES[name],
                data=_artifact_data(name),
            )
        )
    return tuple(artifacts)


def _service(tmp_path: Path):
    artifacts = _artifact_module()
    packages = _package_module()
    return packages.EpisodePackageService(
        artifact_store=artifacts.FilesystemArtifactStore(tmp_path / "artifacts"),
        package_root=tmp_path / "packages",
    )


def _commit(context, *, episode_version_id: str = EPISODE_VERSION_ID):
    context.values["package"] = context.values["service"].commit(
        episode_version_id,
        context.values["artifacts"],
        context.values["provenance"],
    )


@pytest.fixture
def package_context(tmp_path):
    """Build isolated filesystem-backed package dependencies per scenario."""
    return {"service": _service(tmp_path), "root": tmp_path}


@given("a complete verified episode artifact set")
def complete_verified_artifact_set(context, package_context):
    artifacts = _artifacts()
    context.values.update(package_context)
    context.values["artifacts"] = artifacts
    context.values["provenance"] = _provenance(wav=artifacts[0].data)


@given(parsers.parse('a verified episode artifact set missing "{name}"'))
def artifact_set_missing_name(context, package_context, name):
    artifacts = _artifacts(omitted=name)
    context.values.update(package_context)
    context.values["artifacts"] = artifacts
    context.values["provenance"] = _provenance(wav=_artifact_data("episode.wav"))


@given(
    parsers.parse('a verified episode artifact set with duplicate "{name}" artifacts')
)
def artifact_set_with_duplicate_name(context, package_context, name):
    artifacts = _artifacts()
    duplicate = next(artifact for artifact in artifacts if artifact.name == name)
    context.values.update(package_context)
    context.values["artifacts"] = (*artifacts, duplicate)
    context.values["provenance"] = _provenance(wav=artifacts[0].data)


@given(parsers.parse('a verified episode artifact set containing unsafe name "{name}"'))
def artifact_set_with_unsafe_name(context, package_context, name):
    artifacts = _artifacts(unsafe_name=name)
    context.values.update(package_context)
    context.values["artifacts"] = artifacts
    context.values["provenance"] = _provenance(wav=artifacts[0].data)


@given("a complete episode artifact set with failed QA evidence")
def complete_artifact_set_with_failed_qa(context, package_context):
    artifacts = _artifacts()
    context.values.update(package_context)
    context.values["artifacts"] = artifacts
    context.values["provenance"] = _provenance(qa="fail", wav=artifacts[0].data)


@given(
    parsers.parse(
        "a complete episode artifact set with critical-token accuracy {accuracy:f}"
    )
)
def complete_artifact_set_with_incomplete_accuracy(context, package_context, accuracy):
    artifacts = _artifacts()
    context.values.update(package_context)
    context.values["artifacts"] = artifacts
    context.values["provenance"] = _provenance(accuracy=accuracy, wav=artifacts[0].data)


@given("two equivalent complete verified episode artifact sets")
def two_equivalent_artifact_sets(context, package_context):
    first = _artifacts()
    second = _artifacts()
    context.values.update(package_context)
    context.values["first_artifacts"] = first
    context.values["second_artifacts"] = second
    context.values["first_provenance"] = _provenance(wav=first[0].data)
    context.values["second_provenance"] = _provenance(wav=second[0].data)


@given("a committed immutable episode package")
def committed_immutable_episode_package(context, package_context):
    complete_verified_artifact_set(context, package_context)
    _commit(context)
    context.values["original"] = context.values["package"]


@given("a stored immutable artifact")
def stored_immutable_artifact(context, package_context):
    artifacts = _artifact_module()
    store = artifacts.FilesystemArtifactStore(package_context["root"] / "artifacts")
    context.values.update(package_context)
    context.values["store"] = store
    context.values["ref"] = store.put("episode.wav", "audio/wav", b"verified WAV bytes")


@when("the immutable episode package is committed")
def immutable_episode_package_is_committed(context):
    try:
        _commit(context)
    except Exception as error:
        context.values["error"] = error


@when("both immutable episode packages are committed")
def both_immutable_episode_packages_are_committed(context):
    service = context.values["service"]
    context.values["first"] = service.commit(
        EPISODE_VERSION_ID,
        context.values["first_artifacts"],
        context.values["first_provenance"],
    )
    context.values["second"] = service.commit(
        EPISODE_VERSION_ID,
        context.values["second_artifacts"],
        context.values["second_provenance"],
    )


@when("the same immutable episode package is committed twice")
def same_immutable_episode_package_is_committed_twice(context):
    _commit(context)
    context.values["first"] = context.values["package"]
    _commit(context)
    context.values["second"] = context.values["package"]


@when("a different package is committed for the same episode version")
def different_package_is_committed_for_same_episode_version(context):
    changed = list(context.values["artifacts"])
    changed[1] = _package_module().PackageArtifact(
        name="episode.mp3",
        media_type="audio/mpeg",
        data=b"different immutable MP3 bytes",
    )
    try:
        context.values["service"].commit(
            EPISODE_VERSION_ID,
            tuple(changed),
            _provenance(wav=changed[0].data),
        )
    except Exception as error:
        context.values["error"] = error


@when("its stored bytes are corrupted")
def stored_bytes_are_corrupted(context):
    ref = context.values["ref"]
    (context.values["root"] / "artifacts" / ref.storage_key).write_bytes(
        b"corrupted bytes"
    )


@then("the package contains the approved nine files in manifest order")
def package_contains_approved_files_in_order(context):
    assert (
        tuple(item["name"] for item in context.values["package"].to_dict()["files"])
        == APPROVED_NAMES
    )


@then("every manifest file records the exact byte count and SHA-256 checksum")
def manifest_files_record_exact_integrity_metadata(context):
    files = context.values["package"].to_dict()["files"]
    expected = {
        artifact.name: artifact.data for artifact in context.values["artifacts"]
    }
    assert all(item["bytes"] == len(expected[item["name"]]) for item in files)
    assert all(
        item["sha256"] == sha256(expected[item["name"]]).hexdigest() for item in files
    )


@then("package commit is rejected")
def package_commit_is_rejected(context):
    assert isinstance(context.values.get("error"), Exception)


@then("their package manifests serialize identically")
def package_manifests_serialize_identically(context):
    first = json.dumps(
        context.values["first"].to_dict(), sort_keys=True, separators=(",", ":")
    )
    second = json.dumps(
        context.values["second"].to_dict(), sort_keys=True, separators=(",", ":")
    )
    assert first == second


@then("the manifest contains only approved schema fields")
def manifest_contains_only_approved_schema_fields(context):
    manifest = context.values["first"].to_dict()
    assert set(manifest) == TOP_LEVEL_SCHEMA_FIELDS
    assert manifest["schema_version"] == "1.0.0"
    assert all(set(item) == FILE_SCHEMA_FIELDS for item in manifest["files"])


@then("the replay returns the original immutable manifest")
def replay_returns_original_immutable_manifest(context):
    assert context.values["second"].to_dict() == context.values["first"].to_dict()


@then("the conflicting package commit is rejected")
def conflicting_package_commit_is_rejected(context):
    assert isinstance(context.values.get("error"), Exception)


@then("the original immutable manifest remains readable")
def original_immutable_manifest_remains_readable(context):
    assert (
        context.values["original"].to_dict()
        == context.values["service"]
        .commit(
            EPISODE_VERSION_ID,
            context.values["artifacts"],
            context.values["provenance"],
        )
        .to_dict()
    )


@then("reading the artifact fails integrity verification")
def reading_artifact_fails_integrity_verification(context):
    with pytest.raises(ValueError):
        context.values["store"].read(context.values["ref"])
