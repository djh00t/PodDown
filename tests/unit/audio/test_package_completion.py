"""Contract tests for atomic package-completion persistence."""

from __future__ import annotations

from dataclasses import replace

import pytest

from poddown.audio.production_workflow import (
    InMemoryPackageCompletionRepository,
    MasterAndFinalMasterQaResult,
    PackageCompletion,
    PackageCompletionConflictError,
    PackageCompletionError,
    PackageObjectReference,
    ProductionPostRenderStageError,
    ProductionWorkflowInputError,
    package_completion_for,
)


def _qa_result() -> MasterAndFinalMasterQaResult:
    """Build validated final-master QA evidence for one episode version."""
    return MasterAndFinalMasterQaResult(
        episode_id="episode-1",
        episode_version_id="version-1",
        master_wav_checksum="a" * 64,
        master_mp3_checksum="b" * 64,
        qa_master_checksum="a" * 64,
        qa_passed=True,
        qa_critical_token_accuracy=1.0,
    )


def _reference(
    name: str,
    checksum: str,
    *,
    byte_count: int = 1,
    media_type: str = "application/octet-stream",
    schema_version: str = "1.0",
) -> PackageObjectReference:
    """Build one immutable object reference without an object-store provider."""
    return PackageObjectReference(
        name=name,
        sha256=checksum,
        storage_key=f"objects/{checksum}",
        byte_count=byte_count,
        media_type=media_type,
        schema_version=schema_version,
    )


def _completion():
    """Build one package completion candidate with checksum-bound references."""
    return package_completion_for(
        _qa_result(),
        wav_object=_reference("episode.wav", "a" * 64),
        mp3_object=_reference("episode.mp3", "b" * 64),
        manifest_object=_reference("package-manifest.json", "c" * 64),
    )


def test_package_completion_commits_refs_and_completed_status_atomically() -> None:
    """Catch a repository exposing refs or completed status independently."""
    repository = InMemoryPackageCompletionRepository()
    completion = _completion()

    assert repository.commit_package_completion(completion) == completion
    assert repository.commit_package_completion(completion) == completion
    assert repository.get("episode-1", "version-1") == completion


def test_package_completion_failure_leaves_no_partial_completed_record() -> None:
    """Catch a failing commit leaving a misleading completed package behind."""
    repository = InMemoryPackageCompletionRepository(fail_next_commit=True)

    with pytest.raises(PackageCompletionError, match="atomic package commit failed"):
        repository.commit_package_completion(_completion())

    assert repository.get("episode-1", "version-1") is None


def test_package_completion_rejects_conflicting_immutable_refs() -> None:
    """Catch a replay that replaces immutable object references or status."""
    repository = InMemoryPackageCompletionRepository()
    completion = _completion()
    repository.commit_package_completion(completion)

    with pytest.raises(PackageCompletionConflictError):
        repository.commit_package_completion(
            replace(
                completion,
                manifest_object=_reference("package-manifest.json", "d" * 64),
            )
        )

    assert repository.get("episode-1", "version-1") == completion


def test_package_completion_rejects_conflicting_immutable_reference_metadata() -> None:
    """Catch a replay that changes metadata for otherwise identical bytes."""
    repository = InMemoryPackageCompletionRepository()
    completion = _completion()
    repository.commit_package_completion(completion)

    with pytest.raises(PackageCompletionConflictError):
        repository.commit_package_completion(
            replace(
                completion,
                manifest_object=_reference(
                    "package-manifest.json",
                    "c" * 64,
                    media_type="application/json",
                ),
            )
        )

    assert repository.get("episode-1", "version-1") == completion


def test_package_completion_serialization_includes_complete_object_metadata() -> None:
    """Catch a persisted completion silently discarding object metadata."""
    completion = _completion()

    assert completion.to_dict()["manifest_object"] == {
        "name": "package-manifest.json",
        "sha256": "c" * 64,
        "storage_key": f"objects/{'c' * 64}",
        "byte_count": 1,
        "media_type": "application/octet-stream",
        "schema_version": "1.0",
    }


def test_package_completion_round_trips_persisted_record() -> None:
    """Persisted package evidence must decode to the same immutable record."""
    completion = _completion()

    assert PackageCompletion.from_dict(completion.to_dict()) == completion


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["wav_object"].update({"byte_count": "1"}),
        lambda payload: payload["final_master_qa"].update({"qa_passed": 1}),
        lambda payload: payload["final_master_qa"].update(
            {"qa_critical_token_accuracy": 1}
        ),
        lambda payload: payload.update({"status": "pending"}),
        lambda payload: payload.pop("episode_id"),
    ],
)
def test_package_completion_rejects_malformed_persisted_record(mutate) -> None:
    """Malformed persisted package evidence must remain fail-closed."""
    payload = _completion().to_dict()
    mutate(payload)

    with pytest.raises(PackageCompletionError):
        PackageCompletion.from_dict(payload)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"name": ""}, "package object name"),
        ({"sha256": "invalid"}, "package object checksum"),
        ({"storage_key": ""}, "package object storage key"),
        ({"byte_count": 0}, "package object byte count"),
        ({"media_type": ""}, "package object media type"),
        ({"schema_version": ""}, "package object schema version"),
    ],
)
def test_package_completion_maps_invalid_object_metadata_to_completion_error(
    kwargs: dict[str, object], message: str
) -> None:
    """Catch object metadata validation leaking a production-stage error type."""
    values: dict[str, object] = {
        "name": "episode.wav",
        "sha256": "a" * 64,
        "storage_key": f"objects/{'a' * 64}",
        "byte_count": 1,
        "media_type": "audio/wav",
        "schema_version": "1.0",
    }
    values.update(kwargs)

    with pytest.raises(PackageCompletionError, match=message) as captured:
        PackageObjectReference(**values)

    assert not isinstance(captured.value, ProductionWorkflowInputError)
    assert not isinstance(captured.value, ProductionPostRenderStageError)


@pytest.mark.parametrize(
    ("name", "checksum", "message"),
    [
        ("episode.wav", "d" * 64, "WAV checksum"),
        ("episode.mp3", "d" * 64, "MP3 checksum"),
    ],
)
def test_package_completion_requires_exact_final_master_checksums(
    name: str, checksum: str, message: str
) -> None:
    """Catch package completion when a final-master object lacks QA binding."""
    reference = _reference(name, checksum)
    kwargs = {
        "wav_object": _reference("episode.wav", "a" * 64),
        "mp3_object": _reference("episode.mp3", "b" * 64),
        "manifest_object": _reference("package-manifest.json", "c" * 64),
    }
    if reference.name == "episode.wav":
        kwargs["wav_object"] = reference
    else:
        kwargs["mp3_object"] = reference

    with pytest.raises(PackageCompletionError, match=message):
        package_completion_for(_qa_result(), **kwargs)
