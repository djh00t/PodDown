from __future__ import annotations

from hashlib import sha256

from pytest_bdd import given, scenarios, then, when

from poddown.agent_mcp import AgentMCPServer, AuthenticatedContext, LocalGateway

scenarios("../features/agent_integrations.feature")


@given("an authenticated tenant MCP server")
def authenticated_server(context):
    gateway = LocalGateway()
    context.values["gateway"] = gateway
    context.values["server"] = AgentMCPServer(gateway, AuthenticatedContext("tenant-a"))


@given("an authenticated tenant MCP server with a sensitive gateway failure")
def sensitive_failure_server(context):
    gateway = LocalGateway()
    gateway.fail_with = "source=SECRET credential=TOKEN voice_id=VOICE provider=PAYLOAD"
    context.values["gateway"] = gateway
    context.values["server"] = AgentMCPServer(gateway, AuthenticatedContext("tenant-a"))


@given("an authenticated tenant MCP server with an authorized audio resource")
def audio_server(context):
    gateway = LocalGateway()
    gateway.episode_resources["episode-1"] = {"audio": "https://local.invalid/audio"}
    context.values["gateway"] = gateway
    context.values["server"] = AgentMCPServer(gateway, AuthenticatedContext("tenant-a"))


@when("the agent calls poddown_preview with Markdown source")
def call_preview(context):
    source = b"# Hello\n\nWorld\n"
    context.values["source"] = source
    context.values["result"] = context.values["server"].call(
        "poddown_preview", {"source": source}
    )


@when("the agent supplies a different tenant ID to poddown_get_episode")
def call_with_tenant(context):
    context.values["result"] = context.values["server"].call(
        "poddown_get_episode", {"episode_id": "episode-1", "tenant_id": "tenant-b"}
    )


@when("the agent calls poddown_publish without fresh approval")
def call_publish(context):
    context.values["result"] = context.values["server"].call(
        "poddown_publish", {"episode_id": "episode-1", "approval": None}
    )


@when("the agent calls poddown_get_status")
def call_status(context):
    context.values["result"] = context.values["server"].call(
        "poddown_get_status", {"episode_id": "episode-1"}
    )


@when("the agent calls poddown_get_episode")
def call_episode(context):
    context.values["result"] = context.values["server"].call(
        "poddown_get_episode", {"episode_id": "episode-1"}
    )


@then("the preview result preserves the exact source digest")
def preview_digest(context):
    assert (
        context.values["result"]["result"]["source_sha256"]
        == sha256(context.values["source"]).hexdigest()
    )


@then("no paid side effect is recorded")
def no_paid_side_effect(context):
    assert context.values["gateway"].side_effects == []


@then("the tool returns a stable invalid-input error")
def invalid_input(context):
    assert context.values["result"] == {
        "error": {
            "code": "invalid_input",
            "message": "tenant scope is server-authenticated",
        }
    }


@then("the gateway receives only the authenticated tenant")
def authenticated_tenant(context):
    assert context.values["gateway"].last_tenant is None
    assert context.values["gateway"].side_effects == []


@then("the tool returns a stable approval-required error")
def approval_required(context):
    assert context.values["result"] == {
        "error": {
            "code": "approval_required",
            "message": "fresh scoped publish approval is required",
        }
    }


@then("no publish side effect is recorded")
def no_publish(context):
    assert context.values["gateway"].side_effects == []


@then("the response contains no source, credential, voice ID, or provider payload")
def redacted(context):
    response = str(context.values["result"])
    for secret in ("SECRET", "TOKEN", "VOICE", "PAYLOAD"):
        assert secret not in response


@then("the response contains a resource link and no inline audio bytes")
def resource_link(context):
    episode = context.values["result"]["result"]
    assert episode["resources"][0]["uri"] == "https://local.invalid/audio"
    assert "audio_bytes" not in episode
