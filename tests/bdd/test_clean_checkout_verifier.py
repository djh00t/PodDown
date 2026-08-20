"""BDD bindings for clean-checkout deterministic demo validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pytest_bdd import given, scenarios, then, when

from scripts.verify_clean_checkout import (
    DemoVerificationError,
    verify_demo_result,
    verify_host_local_demo_result,
)

scenarios("../features/clean_checkout_verifier.feature")


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


@given("a valid deterministic demo result")
def valid_result(context: Any) -> None:
    context.values["result"] = _result()


@given("a deterministic demo result with provider ASR evidence")
def provider_asr_result(context: Any) -> None:
    result = _result()
    result["transcript_evidence"] = "provider-asr"
    context.values["result"] = result


@given("a valid host-local demo result and published media evidence")
def valid_host_local_result(context: Any, tmp_path: Path) -> None:
    result = _result()
    result["mode"] = "local-system-tts-demo"
    result["publication_path"] = "filesystem:reference-demo:episode"
    context.values["result"] = result
    context.values["output"] = tmp_path / "host-local-output"
    (context.values["output"] / "published").mkdir(parents=True)
    (context.values["output"] / "published" / "episode.mp3").write_bytes(
        b"host-local-mp3"
    )
    (context.values["output"] / "published" / "provenance.json").write_text(
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


@given("a host-local demo result with provider ASR evidence")
def host_local_provider_asr_result(context: Any, tmp_path: Path) -> None:
    valid_host_local_result(context, tmp_path)
    context.values["result"]["transcript_evidence"] = "provider-asr"


@when("the demo result is verified")
def verify_result(context: Any) -> None:
    try:
        context.values["error"] = None
        context.values["verified"] = verify_demo_result(context.values["result"])
    except DemoVerificationError as error:
        context.values["error"] = error
        context.values["verified"] = None


@when("the host-local demo result is verified")
def verify_host_local_result(context: Any) -> None:
    try:
        context.values["error"] = None
        context.values["verified"] = verify_host_local_demo_result(
            context.values["result"],
            context.values["output"],
            mp3_inspector=lambda _path: {
                "codec_name": "mp3",
                "sample_rate": "44100",
                "channels": 1,
                "duration": "650.031",
            },
        )
    except DemoVerificationError as error:
        context.values["error"] = error
        context.values["verified"] = None


@then("the clean-checkout demo evidence is accepted")
def result_accepted(context: Any) -> None:
    assert context.values["error"] is None
    assert context.values["verified"] is True


@then("the clean-checkout demo evidence is rejected")
def result_rejected(context: Any) -> None:
    assert isinstance(context.values["error"], DemoVerificationError)
    assert "provider" in str(context.values["error"])


@then("the clean-checkout listening evidence is accepted")
def host_local_result_accepted(context: Any) -> None:
    assert context.values["error"] is None
    assert context.values["verified"] is True


@then("the clean-checkout listening evidence is rejected")
def host_local_result_rejected(context: Any) -> None:
    assert isinstance(context.values["error"], DemoVerificationError)
    assert "provider" in str(context.values["error"])
