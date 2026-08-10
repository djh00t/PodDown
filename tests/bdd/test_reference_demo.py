"""Acceptance bindings for the deterministic local reference episode."""

from __future__ import annotations

from pytest_bdd import given, scenarios, then, when

from poddown.packages import REQUIRED_PACKAGE_ARTIFACTS

scenarios("../features/reference_demo.feature")


@given("an empty reference demo output directory")
def empty_output(context, tmp_path):
    context.values["output_dir"] = tmp_path / "reference-demo"


@when("the reference episode demo is run")
def run_demo(context):
    from poddown.demo import run_reference_demo

    context.values["result"] = run_reference_demo(context.values["output_dir"])


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


@then("the package and publication identities are unchanged")
def stable_identities(context):
    first = context.values["first"].to_dict()
    resumed = context.values["result"].to_dict()
    assert resumed["package_manifest_sha256"] == first["package_manifest_sha256"]
    assert resumed["publication_path"] == first["publication_path"]
