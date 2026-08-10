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
