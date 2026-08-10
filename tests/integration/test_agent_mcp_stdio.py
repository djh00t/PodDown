from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest


def test_stdio_supports_tools_list_and_tool_call_without_external_services():
    environment = {**os.environ, "PODDOWN_TENANT_ID": "tenant-a"}
    process = subprocess.run(
        [sys.executable, "-m", "poddown.agent_mcp_stdio"],
        input=(
            json.dumps(
                {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}}
            )
            + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            + "\n"
            + json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "poddown_preview",
                        "arguments": {"source": "# Hi"},
                    },
                }
            )
            + "\n"
        ),
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )
    responses = [json.loads(line) for line in process.stdout.splitlines()]
    assert process.returncode == 0
    assert responses[0]["result"]["protocolVersion"]
    assert responses[0]["result"]["capabilities"]["tools"] == {}
    assert len(responses[1]["result"]["tools"]) == 5
    assert responses[1]["result"]["tools"][0]["inputSchema"]
    assert responses[2]["result"]["structuredContent"]["side_effect"] == "none"
    assert responses[2]["result"]["content"][0]["type"] == "text"


def test_stdio_rejects_null_arguments_and_continues_session():
    environment = {**os.environ, "PODDOWN_TENANT_ID": "tenant-a"}
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "poddown_preview", "arguments": None},
        },
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "poddown_preview", "arguments": {"source": "# Hi"}},
        },
    ]
    process = subprocess.run(
        [sys.executable, "-m", "poddown.agent_mcp_stdio"],
        input="\n".join(json.dumps(request) for request in requests) + "\n",
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )
    responses = [json.loads(line) for line in process.stdout.splitlines()]
    assert process.returncode == 0
    assert responses[1]["result"]["isError"] is True
    assert responses[2]["result"]["structuredContent"]["side_effect"] == "none"


@pytest.mark.parametrize(
    ("invalid_line", "expected_error"),
    [
        (
            '{"jsonrpc": ',
            {"code": -32700, "message": "Parse error"},
        ),
        (
            json.dumps(["not", "a", "request"]),
            {"code": -32600, "message": "Invalid Request"},
        ),
    ],
)
def test_stdio_returns_framing_error_and_continues_with_valid_request(
    invalid_line, expected_error
):
    environment = {**os.environ, "PODDOWN_TENANT_ID": "tenant-a"}
    valid_request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    process = subprocess.run(
        [sys.executable, "-m", "poddown.agent_mcp_stdio"],
        input=invalid_line + "\n" + json.dumps(valid_request) + "\n",
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )
    responses = [json.loads(line) for line in process.stdout.splitlines()]
    assert process.returncode == 0
    assert process.stderr == ""
    assert responses[0] == {"jsonrpc": "2.0", "id": None, "error": expected_error}
    assert responses[1]["id"] == 1
    assert responses[1]["result"]["protocolVersion"]


def test_stdio_fails_closed_without_authenticated_tenant():
    environment = {
        key: value for key, value in os.environ.items() if key != "PODDOWN_TENANT_ID"
    }
    process = subprocess.run(
        [sys.executable, "-m", "poddown.agent_mcp_stdio"],
        input="",
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )
    assert process.returncode == 2
    assert process.stderr.strip() == "authenticated tenant context is required"


def test_stdio_registers_trusted_environment_approval_without_printing_token():
    token = "trusted-token-not-for-output"
    environment = {
        **os.environ,
        "PODDOWN_TENANT_ID": "tenant-a",
        "PODDOWN_APPROVAL_TOKEN": token,
        "PODDOWN_APPROVAL_EPISODE_ID": "episode-1",
        "PODDOWN_APPROVAL_EXPIRES_AT": "2099-01-01T00:00:00+00:00",
    }
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "poddown_publish",
            "arguments": {"episode_id": "episode-1", "approval_id": token},
        },
    }
    process = subprocess.run(
        [sys.executable, "-m", "poddown.agent_mcp_stdio"],
        input=json.dumps(request) + "\n",
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )
    assert process.returncode == 0
    assert (
        json.loads(process.stdout)["result"]["structuredContent"]["side_effect"]
        == "external_publish"
    )
    assert token not in process.stdout
    assert token not in process.stderr


def test_stdio_does_not_authorize_model_fresh_flag_without_trusted_environment():
    environment = {**os.environ, "PODDOWN_TENANT_ID": "tenant-a"}
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "poddown_publish",
            "arguments": {
                "episode_id": "episode-1",
                "approval_id": "model-token",
                "fresh": True,
                "nonce": "model-nonce",
            },
        },
    }
    process = subprocess.run(
        [sys.executable, "-m", "poddown.agent_mcp_stdio"],
        input=json.dumps(request) + "\n",
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )
    assert json.loads(process.stdout)["result"]["isError"] is True
