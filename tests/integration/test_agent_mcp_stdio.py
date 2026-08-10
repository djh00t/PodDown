from __future__ import annotations

import json
import os
import subprocess
import sys


def test_stdio_supports_tools_list_and_tool_call_without_external_services():
    environment = {**os.environ, "PODDOWN_TENANT_ID": "tenant-a"}
    process = subprocess.run(
        [sys.executable, "-m", "poddown.agent_mcp_stdio"],
        input=(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
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
    assert len(responses[0]["result"]["tools"]) == 5
    assert responses[1]["result"]["side_effect"] == "none"


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
    assert json.loads(process.stdout)["result"]["side_effect"] == "external_publish"
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
    assert json.loads(process.stdout)["error"]["code"] == "invalid_input"
