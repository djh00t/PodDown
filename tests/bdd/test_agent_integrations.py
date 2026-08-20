from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from pytest_bdd import given, scenarios, then, when

from poddown.agent_mcp import (
    AgentMCPServer,
    AuthenticatedContext,
    InMemoryApprovalRegistry,
    LocalGateway,
)

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


@given("an authenticated tenant MCP server with a trusted approval registry")
def approval_server(context):
    now = datetime(2026, 8, 10, tzinfo=UTC)
    registry = InMemoryApprovalRegistry(clock=lambda: now)
    registry.issue(
        "tenant-a",
        "episode-1",
        "approval-1",
        target_id="target-1",
        expires_at=now + timedelta(minutes=5),
    )
    gateway = LocalGateway()
    context.values.update(
        gateway=gateway,
        server=AgentMCPServer(
            gateway, AuthenticatedContext("tenant-a"), approval_verifier=registry
        ),
    )


@when("the agent calls poddown_preview with Markdown source")
def call_preview(context):
    source = "# Hello\n\nWorld\n"
    context.values["source"] = source.encode("utf-8")
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
        "poddown_publish",
        {
            "episode_id": "episode-1",
            "approval_id": "missing",
            "target_id": "target-1",
        },
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


@when("the agent presents a valid approval and replays it")
def replay_approval(context):
    arguments = {
        "episode_id": "episode-1",
        "approval_id": "approval-1",
        "target_id": "target-1",
    }
    context.values["first_publish"] = context.values["server"].call(
        "poddown_publish", arguments
    )
    context.values["replayed_publish"] = context.values["server"].call(
        "poddown_publish", arguments
    )


@when("the agent presents a missing or stale approval")
def stale_approval(context):
    context.values["result"] = context.values["server"].call(
        "poddown_publish",
        {
            "episode_id": "episode-1",
            "approval_id": "missing",
            "target_id": "target-1",
        },
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


@then("the first publish succeeds and the replay is rejected")
def replay_rejected(context):
    assert (
        context.values["first_publish"]["result"]["side_effect"] == "external_publish"
    )
    assert context.values["replayed_publish"]["error"]["code"] == "approval_required"


@then("publish is rejected without a side effect")
def stale_rejected(context):
    assert context.values["result"]["error"]["code"] == "approval_required"
    assert context.values["gateway"].side_effects == []


@when("the MCP client initializes and lists tools")
def initialize_and_list(context):
    context.values["handshake"] = {
        "protocolVersion": "2025-11-25",
        "capabilities": {"tools": {}},
    }
    context.values["listed_tools"] = [
        {"name": name, "description": schema["description"], "inputSchema": schema}
        for name, schema in context.values["server"].schemas().items()
    ]


@then("the handshake returns the supported protocol and capabilities")
def handshake_shape(context):
    assert context.values["handshake"]["protocolVersion"]
    assert context.values["handshake"]["capabilities"]["tools"] == {}


@then("tools are returned as MCP tool objects")
def tool_object_shape(context):
    assert isinstance(context.values["listed_tools"], list)
    assert {"name", "description", "inputSchema"} <= set(
        context.values["listed_tools"][0]
    )


@when("the MCP client calls preview through stdio")
def stdio_preview(context):
    result = context.values["server"].call("poddown_preview", {"source": "# Hi"})
    context.values["stdio_result"] = {
        "content": [{"type": "text", "text": str(result["result"])}],
        "structuredContent": result["result"],
    }


@then("the response contains content and structured content")
def call_tool_result_shape(context):
    assert context.values["stdio_result"]["content"]
    assert context.values["stdio_result"]["structuredContent"]


@when("the MCP client sends null tool arguments followed by a valid call")
def null_then_valid(context):
    context.values["null_result"] = context.values["server"].call(
        "poddown_preview", None
    )
    context.values["valid_result"] = context.values["server"].call(
        "poddown_preview", {"source": "# Hi"}
    )


@then("stdio returns an invalid-parameters result and continues")
def null_result_shape(context):
    assert context.values["null_result"]["error"]["code"] == "invalid_input"
    assert context.values["valid_result"]["result"]["side_effect"] == "none"


@when("the MCP client sends malformed JSON-RPC framing followed by initialize")
def malformed_framing_then_initialize(context):
    environment = {**os.environ, "PODDOWN_TENANT_ID": "tenant-a"}
    process = subprocess.run(
        [sys.executable, "-m", "poddown.agent_mcp_stdio"],
        input='{"jsonrpc": \n'
        + json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        + "\n",
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )
    context.values["stdio_process"] = process
    context.values["stdio_responses"] = [
        json.loads(line) for line in process.stdout.splitlines()
    ]


@then("stdio returns a framing error and continues")
def framing_error_then_continue(context):
    process = context.values["stdio_process"]
    responses = context.values["stdio_responses"]
    assert process.returncode == 0
    assert process.stderr == ""
    assert responses[0] == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32700, "message": "Parse error"},
    }
    assert responses[1]["id"] == 1
    assert responses[1]["result"]["protocolVersion"]
