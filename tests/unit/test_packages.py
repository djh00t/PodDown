"""Unit coverage for immutable episode-package assembly."""

import json
import math
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest

from poddown.artifacts import FilesystemArtifactStore
from poddown.packages import (
    EpisodePackageService,
    PackageArtifact,
    PackageError,
    PackageProvenance,
    manifest_sha256_for,
    package_sha256_for,
)

REQUIRED_ARTIFACTS = (
    ("episode.wav", "audio/wav", b"verified wav bytes"),
    ("episode.mp3", "audio/mpeg", b"verified mp3 bytes"),
    ("transcript.txt", "text/plain", b"Verified transcript."),
    ("transcript.vtt", "text/vtt", b"WEBVTT\n\n"),
    ("chapters.json", "application/json", b'{"chapters":[]}'),
    ("show-notes.md", "text/markdown", b"# Verified notes\n"),
    ("qa-report.json", "application/json", b'{"passed":true}'),
    ("provenance.json", "application/json", b'{"source":"fixture"}'),
    ("render-manifest.json", "application/json", b'{"segments":[]}'),
)


def package_artifacts() -> tuple[PackageArtifact, ...]:
    """Build the approved package members using literal deterministic bytes."""
    return tuple(PackageArtifact(*artifact) for artifact in REQUIRED_ARTIFACTS)


def provenance(**overrides: object) -> PackageProvenance:
    """Build passing provenance with one explicit gate override."""
    values: dict[str, object] = {
        "source_sha256": "a" * 64,
        "script_version": 1,
        "profile_version": "spoken-word-v1",
        "renderer": "deterministic-renderer-v1",
        "qa": "pass",
        "critical_token_accuracy": 1.0,
        "final_sha256": sha256(b"verified wav bytes").hexdigest(),
        "details": {"lexicon_version": "v1", "provider": "fixture"},
    }
    values.update(overrides)
    return PackageProvenance(**values)  # type: ignore[arg-type]


def service(tmp_path) -> EpisodePackageService:
    """Build a real filesystem-backed package service for each focused test."""
    return EpisodePackageService(
        FilesystemArtifactStore(tmp_path / "artifacts"), tmp_path / "packages"
    )


def test_commit_serializes_exact_manifest_in_canonical_member_order(tmp_path):
    """Catch manifests that omit exact bytes, hashes, or canonical ordering."""
    episode_version_id = str(uuid4())

    package = service(tmp_path).commit(
        episode_version_id, tuple(reversed(package_artifacts())), provenance()
    )

    assert package.to_dict() == {
        "schema_version": "1.0.0",
        "episode_version_id": episode_version_id,
        "files": [
            {
                "name": name,
                "media_type": media_type,
                "bytes": len(data),
                "sha256": sha256(data).hexdigest(),
            }
            for name, media_type, data in REQUIRED_ARTIFACTS
        ],
        "provenance": {
            "source_sha256": "a" * 64,
            "script_version": 1,
            "profile_version": "spoken-word-v1",
            "renderer": "deterministic-renderer-v1",
            "qa": "pass",
            "critical_token_accuracy": 1.0,
            "final_sha256": sha256(b"verified wav bytes").hexdigest(),
            "details": {"lexicon_version": "v1", "provider": "fixture"},
        },
    }


def test_manifest_sha256_is_the_canonical_manifest_digest_not_an_artifact_digest(
    tmp_path,
):
    package = service(tmp_path).commit(str(uuid4()), package_artifacts(), provenance())

    assert (
        manifest_sha256_for(package)
        == sha256(
            json.dumps(
                package.to_dict(), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
    )
    assert manifest_sha256_for(package) != package.provenance.final_sha256


def test_package_sha256_is_a_canonical_digest_of_all_package_bytes(tmp_path):
    """Keep generic package bytes distinct from the WAV and manifest digests."""
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    packages = EpisodePackageService(store, tmp_path / "packages")
    package = packages.commit(str(uuid4()), package_artifacts(), provenance())

    package_digest = package_sha256_for(package, store)

    assert len(package_digest) == 64
    assert package_digest != package.provenance.final_sha256
    assert package_digest != manifest_sha256_for(package)

    changed = tuple(
        PackageArtifact(
            artifact.name,
            artifact.media_type,
            b"changed notes" if artifact.name == "show-notes.md" else artifact.data,
        )
        for artifact in package_artifacts()
    )
    changed_package = packages.commit(
        str(uuid4()),
        changed,
        provenance(final_sha256=sha256(b"verified wav bytes").hexdigest()),
    )
    assert package_sha256_for(changed_package, store) != package_digest


@pytest.mark.parametrize(
    "artifact_factory",
    [
        lambda: package_artifacts()[:-1],
        lambda: package_artifacts()[:-1] + (package_artifacts()[0],),
        lambda: (
            package_artifacts()[:-1]
            + (PackageArtifact("../episode.wav", "audio/wav", b"unsafe"),)
        ),
    ],
    ids=["missing-required", "duplicate-name", "unsafe-name"],
)
def test_commit_rejects_invalid_required_artifact_sets(tmp_path, artifact_factory):
    """Catch commits that accept missing, duplicate, or unsafe package members."""
    packages = service(tmp_path)
    episode_version_id = str(uuid4())

    with pytest.raises(ValueError):
        packages.commit(episode_version_id, artifact_factory(), provenance())

    assert packages.get(episode_version_id) is None


@pytest.mark.parametrize(
    "provenance_factory",
    [
        lambda: provenance(source_sha256="invalid"),
        lambda: provenance(script_version=0),
        lambda: provenance(profile_version=""),
        lambda: provenance(renderer=""),
        lambda: provenance(qa="fail"),
        lambda: provenance(critical_token_accuracy=0.99),
        lambda: provenance(final_sha256="b" * 64),
    ],
    ids=[
        "source-checksum",
        "script-version",
        "profile-version",
        "renderer",
        "qa",
        "critical-token-accuracy",
        "final-checksum",
    ],
)
def test_commit_rejects_failed_or_invalid_provenance_gates(
    tmp_path, provenance_factory
):
    """Catch package commits that bypass immutable QA or final-master gates."""
    packages = service(tmp_path)
    episode_version_id = str(uuid4())

    with pytest.raises(ValueError):
        packages.commit(episode_version_id, package_artifacts(), provenance_factory())

    assert packages.get(episode_version_id) is None


def test_identical_commit_replays_the_existing_immutable_manifest(tmp_path):
    """Catch identical replays that rewrite rather than return stored evidence."""
    packages = service(tmp_path)
    episode_version_id = str(uuid4())

    first = packages.commit(episode_version_id, package_artifacts(), provenance())
    manifest_path = tmp_path / "packages" / f"{episode_version_id}.json"
    original_bytes = manifest_path.read_bytes()
    original_mtime = manifest_path.stat().st_mtime_ns
    second = packages.commit(episode_version_id, package_artifacts(), provenance())

    assert second == first
    assert packages.get(episode_version_id) == first
    assert manifest_path.read_bytes() == original_bytes
    assert manifest_path.stat().st_mtime_ns == original_mtime


def test_replay_canonicalizes_json_native_provenance_details(tmp_path):
    """Catch replay conflicts caused by JSON changing tuple and mapping key shapes."""
    packages = service(tmp_path)
    episode_version_id = str(uuid4())
    provenance_details = {1: ("primary", "fallback"), "nested": {2: "value"}}

    first = packages.commit(
        episode_version_id,
        package_artifacts(),
        provenance(details=provenance_details),
    )
    replay = packages.commit(
        episode_version_id,
        package_artifacts(),
        provenance(details=provenance_details),
    )

    assert replay == first
    assert replay.provenance.details == {
        "1": ["primary", "fallback"],
        "nested": {"2": "value"},
    }


@pytest.mark.parametrize(
    "details",
    [{"invalid": object()}, {"invalid": math.nan}],
    ids=["non-json", "nan"],
)
def test_commit_rejects_non_json_provenance_details_without_writing(tmp_path, details):
    """Catch provider evidence that escapes the package error boundary."""
    packages = service(tmp_path)
    episode_version_id = str(uuid4())

    with pytest.raises(PackageError, match="JSON"):
        packages.commit(
            episode_version_id,
            package_artifacts(),
            provenance(details={"invalid": object()}),
        )

    assert packages.get(episode_version_id) is None


def test_manifest_matches_repository_episode_package_schema_constraints(tmp_path):
    """Keep generated output aligned with the approved repository schema."""
    package = service(tmp_path).commit(str(uuid4()), package_artifacts(), provenance())
    manifest = package.to_dict()
    schema = json.loads(
        Path(
            "specs/001-core-audio-vertical-slice/contracts/episode-package.schema.json"
        ).read_text(encoding="utf-8")
    )

    assert set(manifest) == set(schema["required"])
    assert manifest["schema_version"] == schema["properties"]["schema_version"]["const"]
    assert len(manifest["files"]) >= schema["properties"]["files"]["minItems"]
    file_properties = schema["properties"]["files"]["items"]["properties"]
    for item in manifest["files"]:
        assert set(item) == set(file_properties)
        assert isinstance(item["bytes"], int)
        assert len(item["sha256"]) == 64
    assert manifest["provenance"]["qa"] == "pass"
    assert manifest["provenance"]["critical_token_accuracy"] == 1.0


def test_conflicting_commit_preserves_the_original_manifest_atomically(tmp_path):
    """Catch a conflicting replay that replaces a previously committed package."""
    packages = service(tmp_path)
    episode_version_id = str(uuid4())
    original = packages.commit(episode_version_id, package_artifacts(), provenance())
    conflicting = package_artifacts()[:-1] + (
        PackageArtifact(
            "render-manifest.json", "application/json", b'{"changed":true}'
        ),
    )

    with pytest.raises(RuntimeError):
        packages.commit(episode_version_id, conflicting, provenance())

    assert packages.get(episode_version_id) == original
