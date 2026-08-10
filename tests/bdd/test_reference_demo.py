"""Acceptance bindings for the deterministic local reference episode."""

from __future__ import annotations

import json
import wave
from decimal import Decimal
from io import BytesIO

from pytest_bdd import given, scenarios, then, when

from poddown.audio import DeterministicLocalRenderer
from poddown.audio.contracts import RenderedAudio, RenderRequest
from poddown.domain import ProviderUsage
from poddown.packages import REQUIRED_PACKAGE_ARTIFACTS

scenarios("../features/reference_demo.feature")


class FakeLocalSpeechRenderer:
    """Render local-speech BDD fixtures without invoking a host speech engine."""

    capabilities = DeterministicLocalRenderer.capabilities
    provider = "host-local"
    model = "host-local-tts-v1"
    mode = "local-system-tts-demo"

    def provenance(self):
        """Return the stable fake engine evidence asserted by this feature."""
        return {"engine": "fake-local-speech", "mode": self.mode}

    async def render(self, request: RenderRequest) -> RenderedAudio:
        """Return a quiet five-second WAV with the requested provider identity."""
        stream = BytesIO()
        with wave.open(stream, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(request.sample_rate_hz)
            output.writeframes((500).to_bytes(2, "little", signed=True) * 220_500)
        audio_bytes = stream.getvalue()
        return RenderedAudio(
            audio_bytes,
            request.provider,
            request.model,
            f"fake-{request.segment_id}-{request.take_index}",
            ProviderUsage(len(request.expected_spoken_text), len(audio_bytes)),
            Decimal("0"),
            "wav",
            request.sample_rate_hz,
        )


@given("an empty reference demo output directory")
def empty_output(context, tmp_path):
    context.values["output_dir"] = tmp_path / "reference-demo"


@when("the reference episode demo is run")
def run_demo(context):
    from poddown.demo import run_reference_demo

    context.values["result"] = run_reference_demo(context.values["output_dir"])


@given("an empty local speech reference demo output directory")
def empty_local_speech_output(context, tmp_path):
    context.values["output_dir"] = tmp_path / "reference-demo-local-speech"


@when("the local speech reference episode demo is run")
def run_local_speech_demo(context):
    from poddown.demo import run_reference_demo

    context.values["result"] = run_reference_demo(
        context.values["output_dir"], renderer=FakeLocalSpeechRenderer()
    )


@then("the result records host-local speech provenance")
def local_speech_provenance(context):
    result = context.values["result"].to_dict()
    package = json.loads(
        next((context.values["output_dir"] / "packages").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    details = package["provenance"]["details"]

    assert result["mode"] == "local-system-tts-demo"
    assert details["provider"]["provider"] == "host-local"
    assert details["provider"]["model"] == "host-local-tts-v1"
    assert details["workflow"]["renderer"]["provenance"]["engine"] == (
        "fake-local-speech"
    )


@then("the result records a validated source profile and two speakers")
def validated_source(context):
    result = context.values["result"].to_dict()
    assert result["mode"] == "deterministic-local-demo"
    assert result["source_sha256"]
    assert result["profile_id"] == "reference-demo-dialogue-v1"
    assert result["target_minutes"] == 12
    assert len(result["speakers"]) == 2
    assert len(set(result["speakers"])) == 2


@then("three takes are rendered for every segment with stable voice bindings")
def takes_and_voices(context):
    result = context.values["result"].to_dict()
    assert result["take_count"] == 3
    assert len(result["segment_ids"]) == len(result["selected_candidate_ids"])
    assert len(result["voice_bindings"]) == len(result["segment_ids"])
    assert set(result["voice_bindings"].values()) == {
        "demo-reference-host-v1",
        "demo-reference-analyst-v1",
    }


@then("one failed segment is regenerated before QA")
def regeneration(context):
    result = context.values["result"].to_dict()
    assert len(result["failed_segment_ids"]) == 1
    assert result["failed_segment_ids"] == result["regenerated_segment_ids"]


@then("usage records the deliberate failed render invocation")
def metered_regeneration(context):
    result = context.values["result"].to_dict()
    assert result["usage"]["render_requests"] == len(result["segment_ids"]) * 3 + 1


@then("final critical-token accuracy is 1.0")
def fidelity(context):
    assert context.values["result"].critical_token_accuracy == 1.0


@then("the package contains the nine required artifacts")
def package(context):
    assert set(context.values["result"].package_artifacts) == set(
        REQUIRED_PACKAGE_ARTIFACTS
    )


@then("publication is a filesystem demo publication")
def publication(context):
    result = context.values["result"].to_dict()
    assert result["publication_path"].startswith("filesystem:")
    assert result["usage"]["render_requests"] > 0
    assert result["cost"] == "0"
    show_notes = next(
        (context.values["output_dir"] / "published").rglob("show-notes.md")
    ).read_text(encoding="utf-8")
    assert (
        "This episode uses synthetic demo presenters and deterministic-local "
        "PodDown fixtures." in show_notes
    )
    publication = json.loads(
        (context.values["output_dir"] / "publication.json").read_text(encoding="utf-8")
    )
    assert publication["disclosure"] == {
        "spoken": False,
        "show_notes": True,
        "platform": False,
    }


@then("the MCP preview reports no side effect")
def mcp_preview(context):
    result = context.values["result"].to_dict()
    preview = result["mcp_preview"]["result"]
    assert preview["side_effect"] == "none"
    assert preview["source_sha256"] == result["source_sha256"]
    assert preview["profile_id"] == result["profile_id"]


@given("a completed reference episode demo")
def completed_demo(context, tmp_path):
    from poddown.demo import run_reference_demo

    context.values["output_dir"] = tmp_path / "reference-demo"
    context.values["first"] = run_reference_demo(context.values["output_dir"])


@when("the reference episode demo is resumed")
def resume_demo(context):
    from poddown.demo import run_reference_demo

    context.values["result"] = run_reference_demo(
        context.values["output_dir"], resume=True
    )


@then("the result reports replayed render takes")
def replayed(context):
    assert context.values["result"].replayed_takes > 0


@then("the persisted publication is scoped to its episode version")
def episode_scoped_publication(context):
    publication = json.loads(
        (context.values["output_dir"] / "publication.json").read_text(encoding="utf-8")
    )
    published_root = (
        context.values["output_dir"]
        / "published"
        / "tenants"
        / publication["tenant_id"]
        / "projects"
        / publication["project_id"]
        / publication["target_id"]
        / publication["episode_version_id"]
    )
    assert published_root.is_dir()
    assert {path.name for path in published_root.iterdir() if path.is_file()} == set(
        REQUIRED_PACKAGE_ARTIFACTS
    )


@then("the package and publication identities are unchanged")
def stable_identities(context):
    first = context.values["first"].to_dict()
    resumed = context.values["result"].to_dict()
    assert resumed["package_manifest_sha256"] == first["package_manifest_sha256"]
    assert resumed["publication_path"] == first["publication_path"]


@given("a completed reference episode demo with persisted evidence")
def completed_demo_with_persisted_evidence(context, tmp_path):
    from poddown.demo import run_reference_demo

    context.values["output_dir"] = tmp_path / "reference-demo"
    run_reference_demo(context.values["output_dir"])


@when("the persisted result accuracy is changed")
def tamper_result_accuracy(context):
    import json

    result_path = context.values["output_dir"] / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["critical_token_accuracy"] = 0.5
    result_path.write_text(json.dumps(result), encoding="utf-8")


@then("resuming the reference episode fails closed")
def resume_fails_closed(context):
    import pytest

    from poddown.demo import run_reference_demo

    with pytest.raises(ValueError, match="result evidence"):
        run_reference_demo(context.values["output_dir"], resume=True)
