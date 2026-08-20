"""Unit tests for the deterministic clean-checkout verifier."""

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.verify_clean_checkout import (
    DemoVerificationError,
    verify_demo_result,
    verify_host_local_demo_result,
)


def _result() -> dict[str, object]:
    return {
        "mode": "deterministic-local-demo",
        "critical_token_accuracy": 1.0,
        "cost": "0",
        "package_artifacts": [
            "episode.wav",
            "episode.mp3",
            "transcript.txt",
            "transcript.vtt",
            "chapters.json",
            "show-notes.md",
            "qa-report.json",
            "provenance.json",
            "render-manifest.json",
        ],
        "package_manifest_sha256": "a" * 64,
        "failed_segment_ids": ["segment-1"],
        "regenerated_segment_ids": ["segment-1"],
    }


def test_valid_demo_result_is_accepted() -> None:
    assert verify_demo_result(_result()) is True


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("mode", "local-system-tts-demo", "mode"),
        ("critical_token_accuracy", 0.99, "accuracy"),
        ("cost", "1.00", "cost"),
        ("package_manifest_sha256", "not-a-digest", "manifest"),
        ("package_artifacts", ["episode.mp3"], "artifacts"),
    ],
)
def test_invalid_demo_result_is_rejected(
    field: str,
    value: object,
    message: str,
) -> None:
    result = _result()
    result[field] = value
    with pytest.raises(DemoVerificationError, match=message):
        verify_demo_result(result)


def test_provider_evidence_is_never_accepted_for_deterministic_demo() -> None:
    result: dict[str, Any] = _result()
    result["transcript_evidence"] = "provider-asr"
    with pytest.raises(DemoVerificationError, match="provider"):
        verify_demo_result(result)


def _host_local_result() -> dict[str, object]:
    result = _result()
    result["mode"] = "local-system-tts-demo"
    result["publication_path"] = "filesystem:reference-demo:episode"
    return result


def _host_local_output(tmp_path: Path) -> Path:
    published = tmp_path / "published"
    published.mkdir()
    (published / "episode.mp3").write_bytes(b"mp3")
    (published / "provenance.json").write_text(
        json.dumps(
            {
                "renderer": "host-local-tts-v1",
                "qa": "pass",
                "details": {
                    "workflow": {
                        "renderer": {
                            "mode": "local-system-tts-demo",
                            "provider": "host-local",
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_host_local_result_requires_media_and_renderer_provenance(
    tmp_path: Path,
) -> None:
    assert (
        verify_host_local_demo_result(
            _host_local_result(),
            _host_local_output(tmp_path),
            mp3_inspector=lambda _path: {
                "codec_name": "mp3",
                "sample_rate": "44100",
                "channels": 1,
                "duration": "650.031",
            },
        )
        is True
    )


def test_host_local_result_accepts_resume_without_new_failure(
    tmp_path: Path,
) -> None:
    result = _host_local_result()
    result["failed_segment_ids"] = []
    result["regenerated_segment_ids"] = []

    assert (
        verify_host_local_demo_result(
            result,
            _host_local_output(tmp_path),
            mp3_inspector=lambda _path: {
                "codec_name": "mp3",
                "sample_rate": "44100",
                "channels": 1,
                "duration": "650.031",
            },
        )
        is True
    )


def test_host_local_result_rejects_non_mono_media(tmp_path: Path) -> None:
    with pytest.raises(DemoVerificationError, match="mono"):
        verify_host_local_demo_result(
            _host_local_result(),
            _host_local_output(tmp_path),
            mp3_inspector=lambda _path: {
                "codec_name": "mp3",
                "sample_rate": "44100",
                "channels": 2,
                "duration": "650.031",
            },
        )
