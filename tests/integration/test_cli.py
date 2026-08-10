"""Offline integration tests for CLI HTTP command behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from poddown import cli


class FakeTransport:
    def __init__(self, responses: list[cli.HttpResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> cli.HttpResponse:
        self.calls.append((method, url, headers, body))
        return self.responses.pop(0)


def test_render_submits_async_create_and_render_with_idempotency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "episode.md"
    source.write_text(
        "---\npoddown:\n  profile: default\n---\n# Demo\n", encoding="utf-8"
    )
    transport = FakeTransport(
        [
            cli.HttpResponse(202, {"episode": {"id": "episode-1"}}),
            cli.HttpResponse(202, {"command_id": "command-1", "state": "queued"}),
        ]
    )
    monkeypatch.setattr(cli, "_transport", transport)

    code = cli.main(
        [
            "render",
            str(source),
            "--tenant-id",
            "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10",
            "--project-id",
            "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12",
            "--endpoint",
            "http://api.test",
            "--idempotency-key",
            "render-1",
            "--json",
        ]
    )

    assert code == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out)["state"] == "queued"
    assert [call[0] for call in transport.calls] == ["POST", "POST"]
    assert transport.calls[1][1].endswith("/v1/episodes/episode-1/render")


def test_wait_interrupt_does_not_cancel_remote_job(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transport = FakeTransport(
        [cli.HttpResponse(200, {"episode_id": "episode-1", "stage": "rendering"})]
    )
    monkeypatch.setattr(cli, "_transport", transport)
    monkeypatch.setattr(
        cli.time, "sleep", lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt)
    )

    code = cli.main(
        [
            "status",
            "episode-1",
            "--endpoint",
            "http://api.test",
            "--tenant-id",
            "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10",
            "--project-id",
            "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12",
            "--wait",
            "--json",
        ]
    )

    assert code == cli.EXIT_INTERRUPTED
    assert all(method != "DELETE" for method, *_ in transport.calls)
    assert json.loads(capsys.readouterr().out)["interrupted"] is True


def test_api_token_is_read_from_environment_and_never_printed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transport = FakeTransport(
        [cli.HttpResponse(200, {"episode_id": "episode-1", "stage": "packaged"})]
    )
    monkeypatch.setattr(cli, "_transport", transport)
    monkeypatch.setenv("PODDOWN_API_TOKEN", "test-token-only-in-header")

    code = cli.main(
        [
            "status",
            "episode-1",
            "--endpoint",
            "http://api.test",
            "--tenant-id",
            "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10",
            "--project-id",
            "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12",
            "--json",
        ]
    )

    assert code == cli.EXIT_OK
    assert transport.calls[0][2]["Authorization"] == (
        "Bearer test-token-only-in-header"
    )
    assert "test-token-only-in-header" not in capsys.readouterr().out
