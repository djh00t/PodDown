"""BDD bindings for the provider-free CLI and workflow boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when

from poddown import cli

scenarios("../features/cli.feature")


@pytest.fixture
def cli_context(tmp_path: Path) -> dict[str, object]:
    return {"tmp_path": tmp_path, "http_calls": 0}


class RecordingTransport:
    """Small deterministic transport for BDD command scenarios."""

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


@given("a valid Markdown source file")
def valid_source(cli_context: dict[str, object]) -> None:
    path = cli_context["tmp_path"] / "episode.md"
    path.write_text(
        "---\npoddown:\n  profile: default\n---\n# Demo\n", encoding="utf-8"
    )
    cli_context["source"] = path


@when("I preview the source as JSON")
def preview_json(
    cli_context: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    cli_context["exit_code"] = cli.main(
        ["preview", str(cli_context["source"]), "--json"]
    )
    cli_context["output"] = capsys.readouterr().out


@then("preview succeeds with resolved profile and no HTTP calls")
def preview_success(cli_context: dict[str, object]) -> None:
    assert cli_context["exit_code"] == cli.EXIT_OK
    assert '"profile":"default"' in cli_context["output"]
    assert cli_context["http_calls"] == 0


@given("a source with frontmatter and project configuration")
def configured_source(cli_context: dict[str, object]) -> None:
    path = cli_context["tmp_path"] / "episode.md"
    path.write_text(
        "---\npoddown:\n  profile: default\n---\n# Demo\n", encoding="utf-8"
    )
    (cli_context["tmp_path"] / ".poddown.yaml").write_text(
        "profile: project\n", encoding="utf-8"
    )
    cli_context["source"] = path


@when("I preview with an explicit profile flag")
def preview_with_flag(
    cli_context: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    cli_context["exit_code"] = cli.main(
        [
            "preview",
            str(cli_context["source"]),
            "--profile",
            "technical-dialogue",
            "--json",
        ]
    )
    cli_context["output"] = capsys.readouterr().out


@then("the flag profile wins in stable JSON output")
def flag_profile_wins(cli_context: dict[str, object]) -> None:
    assert cli_context["exit_code"] == cli.EXIT_OK
    assert '"profile":"technical-dialogue"' in cli_context["output"]


@when("I preview with an explicit endpoint as JSON")
def preview_with_endpoint(
    cli_context: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    cli_context["exit_code"] = cli.main(
        [
            "preview",
            str(cli_context["source"]),
            "--endpoint",
            "http://api.test",
            "--json",
        ]
    )
    cli_context["output"] = capsys.readouterr().out


@then("preview reports the endpoint without HTTP calls")
def preview_endpoint_success(cli_context: dict[str, object]) -> None:
    assert cli_context["exit_code"] == cli.EXIT_OK
    assert json.loads(cli_context["output"])["endpoint"] == "http://api.test"
    assert cli_context["http_calls"] == 0


@when("I render the source through the fake API as JSON")
def render_with_fake_api(
    cli_context: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transport = RecordingTransport(
        [
            cli.HttpResponse(202, {"episode": {"id": "episode-1"}}),
            cli.HttpResponse(202, {"command_id": "command-1", "state": "queued"}),
        ]
    )
    monkeypatch.setattr(cli, "_transport", transport)
    cli_context["transport"] = transport
    cli_context["exit_code"] = cli.main(
        [
            "render",
            str(cli_context["source"]),
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
    cli_context["output"] = capsys.readouterr().out


@then("render returns a queued receipt after two API calls")
def render_success(cli_context: dict[str, object]) -> None:
    transport = cli_context["transport"]
    assert cli_context["exit_code"] == cli.EXIT_OK
    assert json.loads(cli_context["output"])["state"] == "queued"
    assert len(transport.calls) == 2


@when("I query status through the fake API as JSON")
def status_with_fake_api(
    cli_context: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transport = RecordingTransport(
        [cli.HttpResponse(200, {"episode_id": "episode-1", "stage": "packaged"})]
    )
    monkeypatch.setattr(cli, "_transport", transport)
    cli_context["transport"] = transport
    cli_context["exit_code"] = cli.main(
        [
            "status",
            "episode-1",
            "--tenant-id",
            "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10",
            "--project-id",
            "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12",
            "--endpoint",
            "http://api.test",
            "--json",
        ]
    )
    cli_context["output"] = capsys.readouterr().out


@then("status returns a packaged state")
def status_success(cli_context: dict[str, object]) -> None:
    assert cli_context["exit_code"] == cli.EXIT_OK
    assert json.loads(cli_context["output"])["stage"] == "packaged"


@when("I publish with confirmation through the fake API as JSON")
def publish_with_fake_api(
    cli_context: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transport = RecordingTransport(
        [cli.HttpResponse(202, {"state": "published", "episode_id": "episode-1"})]
    )
    monkeypatch.setattr(cli, "_transport", transport)
    cli_context["transport"] = transport
    cli_context["exit_code"] = cli.main(
        [
            "publish",
            "episode-1",
            "--tenant-id",
            "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10",
            "--project-id",
            "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12",
            "--endpoint",
            "http://api.test",
            "--confirm",
            "--json",
        ]
    )
    cli_context["output"] = capsys.readouterr().out


@then("publish returns success with publish authorization")
def publish_success(cli_context: dict[str, object]) -> None:
    transport = cli_context["transport"]
    assert cli_context["exit_code"] == cli.EXIT_OK
    assert json.loads(cli_context["output"])["state"] == "published"
    assert transport.calls[0][2]["X-Publish-Authorization"] == "true"


@when("I publish without confirmation")
def publish_without_confirmation(
    cli_context: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    cli_context["exit_code"] = cli.main(
        [
            "publish",
            "episode-1",
            "--tenant-id",
            "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10",
            "--project-id",
            "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12",
            "--endpoint",
            "http://api.test",
            "--json",
        ]
    )
    cli_context["output"] = capsys.readouterr().out


@then("publish fails with an authorization exit and no HTTP calls")
def publish_requires_confirmation(cli_context: dict[str, object]) -> None:
    assert cli_context["exit_code"] == cli.EXIT_AUTH
    assert cli_context["http_calls"] == 0


@given("a local episode package")
def local_package(cli_context: dict[str, object]) -> None:
    source = cli_context["tmp_path"] / "episode.tar"
    source.write_bytes(b"package")
    cli_context["package"] = source
    cli_context["destination"] = cli_context["tmp_path"] / "out" / "episode.tar"


@when("I install the package with a wrong checksum")
def install_wrong_checksum(
    cli_context: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    cli_context["exit_code"] = cli.main(
        [
            "package-install",
            str(cli_context["package"]),
            "--output",
            str(cli_context["destination"]),
            "--sha256",
            "0" * 64,
            "--json",
        ]
    )
    cli_context["output"] = capsys.readouterr().out


@then("installation fails without writing the destination")
def install_rejects_checksum(cli_context: dict[str, object]) -> None:
    assert cli_context["exit_code"] == cli.EXIT_WORKFLOW
    assert not cli_context["destination"].exists()


@given("the pull request validation action definition")
def action_definition(cli_context: dict[str, object]) -> None:
    workflow = Path(".github/workflows/poddown-validate.yaml").read_text(
        encoding="utf-8"
    )
    action = Path(".github/actions/poddown-preview/action.yml").read_text(
        encoding="utf-8"
    )
    cli_context["action"] = workflow + "\n" + action


@then("it keeps pull-request preview separate from protected operations")
def action_is_safe(cli_context: dict[str, object]) -> None:
    action = cli_context["action"]
    assert "poddown preview" in action
    assert "pull_request" in action
    assert "workflow_dispatch:" in action
    assert "concurrency:" in action
    assert "poddown-render-approval" in action
    assert "PODDOWN_API_TOKEN" in action
    preview_job = action.split("approved-render:", 1)[0]
    assert "secrets." not in preview_job
    assert "PODDOWN_API_TOKEN" not in preview_job
