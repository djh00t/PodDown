"""BDD coverage for atomic package-completion persistence."""

from __future__ import annotations

from pytest_bdd import given, scenarios, then, when

from tests.bdd.conftest import ScenarioContext

scenarios("../features/package_completion.feature")


def _completion():
    """Build one complete checksum-bound package commit request."""
    from poddown.audio.production_workflow import (
        MasterAndFinalMasterQaResult,
        PackageObjectReference,
        package_completion_for,
    )

    qa_result = MasterAndFinalMasterQaResult(
        episode_id="episode-1",
        episode_version_id="version-1",
        master_wav_checksum="a" * 64,
        master_mp3_checksum="b" * 64,
        qa_master_checksum="a" * 64,
        qa_passed=True,
        qa_critical_token_accuracy=1.0,
    )
    return package_completion_for(
        qa_result,
        wav_object=PackageObjectReference(
            "episode.wav", "a" * 64, "objects/wav", 1, "audio/wav", "1.0"
        ),
        mp3_object=PackageObjectReference(
            "episode.mp3", "b" * 64, "objects/mp3", 1, "audio/mpeg", "1.0"
        ),
        manifest_object=PackageObjectReference(
            "package-manifest.json",
            "c" * 64,
            "objects/manifest",
            1,
            "application/json",
            "1.0",
        ),
    )


@given("final-master QA evidence and package object references")
def package_completion_candidate(context: ScenarioContext) -> None:
    """Store a complete candidate and deterministic local repository."""
    from poddown.audio.production_workflow import InMemoryPackageCompletionRepository

    context.values["completion"] = _completion()
    context.values["repository"] = InMemoryPackageCompletionRepository()


@given("final-master QA evidence and a failing package completion repository")
def failing_package_completion_repository(context: ScenarioContext) -> None:
    """Store one candidate with a deterministic atomic-write failure."""
    from poddown.audio.production_workflow import InMemoryPackageCompletionRepository

    context.values["completion"] = _completion()
    context.values["repository"] = InMemoryPackageCompletionRepository(
        fail_next_commit=True
    )


@when("the package completion is committed twice")
def commit_package_completion_twice(context: ScenarioContext) -> None:
    """Replay the exact immutable commit through the single repository operation."""
    repository = context.values["repository"]
    completion = context.values["completion"]
    context.values["first"] = repository.commit_package_completion(completion)
    context.values["second"] = repository.commit_package_completion(completion)


@when("the package completion is committed")
def commit_package_completion(context: ScenarioContext) -> None:
    """Capture a typed commit failure without fabricating terminal status."""
    try:
        context.values["repository"].commit_package_completion(
            context.values["completion"]
        )
    except Exception as error:
        context.values["error"] = error


@when("a package object reference has empty schema-version metadata")
def invalid_package_object_metadata(context: ScenarioContext) -> None:
    """Capture package-bound metadata validation through its public boundary."""
    from poddown.audio.production_workflow import PackageObjectReference

    try:
        PackageObjectReference(
            "episode.wav", "a" * 64, "objects/wav", 1, "audio/wav", ""
        )
    except Exception as error:
        context.values["error"] = error


@then("the same completed package record is returned")
def same_completed_record_is_returned(context: ScenarioContext) -> None:
    """Require deterministic replay of the exact immutable package commit."""
    assert context.values["first"] == context.values["completion"]
    assert context.values["second"] == context.values["completion"]


@then("no completed package record exists")
def no_completed_package_record_exists(context: ScenarioContext) -> None:
    """Require a failed atomic commit to leave no partial completion record."""
    from poddown.audio.production_workflow import PackageCompletionError

    assert isinstance(context.values["error"], PackageCompletionError)
    assert context.values["repository"].get("episode-1", "version-1") is None


@then("the metadata rejection is a package completion error")
def metadata_rejection_is_a_package_completion_error(
    context: ScenarioContext,
) -> None:
    """Require package-completion callers to receive only the boundary error."""
    from poddown.audio.production_workflow import (
        PackageCompletionError,
        ProductionPostRenderStageError,
        ProductionWorkflowInputError,
    )

    error = context.values["error"]
    assert isinstance(error, PackageCompletionError)
    assert not isinstance(error, ProductionWorkflowInputError)
    assert not isinstance(error, ProductionPostRenderStageError)
