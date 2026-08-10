"""Unit contracts for the PodDown CLI boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from poddown import cli


def test_resolve_config_uses_flag_then_frontmatter_then_project_then_user(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "---\npoddown:\n  profile: frontmatter\n---\n# Demo\n"
    project = tmp_path / ".poddown.yaml"
    project.write_text("profile: project\nendpoint: http://project\n", encoding="utf-8")
    user_dir = tmp_path / "user-config"
    user_dir.mkdir()
    (user_dir / "config.yaml").write_text(
        "profile: user\nendpoint: http://user\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli.Path, "home", lambda: user_dir)

    config = cli.resolve_config(
        source,
        cwd=tmp_path,
        profile_flag="flag",
        endpoint_flag=None,
        output_dir_flag=None,
    )

    assert config.profile == "flag"
    assert config.endpoint == "http://project"


def test_resolve_config_uses_api_endpoint_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Protected workflow configuration must not fall back to localhost."""
    monkeypatch.setenv("PODDOWN_API_ENDPOINT", "https://api.example.test/")

    config = cli.resolve_config(
        "# Demo\n",
        profile_flag=None,
        endpoint_flag=None,
        output_dir_flag=None,
    )

    assert config.endpoint == "https://api.example.test"


def test_preview_json_is_sorted_and_contains_no_source_text(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "episode.md"
    source.write_text(
        "---\npoddown:\n  profile: technical-dialogue\n---\n# Demo\n",
        encoding="utf-8",
    )
    config = tmp_path / "poddown.toml"
    config.write_text('profiles = ["technical-dialogue"]\n', encoding="utf-8")

    assert (
        cli.main(["preview", str(source), "--config", str(config), "--json"])
        == cli.EXIT_OK
    )
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert list(payload) == sorted(payload)
    assert payload["profile_id"] == "technical-dialogue"
    assert "# Demo" not in output


def test_install_verified_package_rejects_checksum_without_partial_write(
    tmp_path: Path,
) -> None:
    source = tmp_path / "package.tar"
    destination = tmp_path / "out" / "package.tar"
    source.write_bytes(b"package")

    with pytest.raises(cli.CliError) as error:
        cli.install_verified_package(source, destination, "0" * 64)

    assert error.value.exit_code == cli.EXIT_WORKFLOW
    assert not destination.exists()


@pytest.mark.parametrize(
    ("status", "exit_code"),
    [(401, cli.EXIT_AUTH), (422, cli.EXIT_VALIDATION), (500, cli.EXIT_WORKFLOW)],
)
def test_http_status_maps_to_stable_exit_codes(status: int, exit_code: int) -> None:
    assert cli.exit_code_for_http_status(status) == exit_code
