"""Subprocess contracts for the offline ``python -m poddown preview`` command."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run_preview(tmp_path: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    environment = os.environ | {"XDG_CONFIG_HOME": str(tmp_path / "xdg")}
    return subprocess.run(
        [sys.executable, "-m", "poddown", "preview", *arguments],
        capture_output=True,
        check=False,
        cwd=tmp_path,
        env=environment,
    )


def _write_source(tmp_path: Path, content: bytes) -> Path:
    path = tmp_path / "episode.md"
    path.write_bytes(content)
    return path


def test_preview_cli_writes_stable_human_summary_and_preserves_source_bytes(tmp_path):
    """Catch a successful preview changing source bytes or reporting provider work."""
    source = b"# Caf\xc3\xa9\n\nA deterministic preview.\n"
    source_path = _write_source(tmp_path, source)
    config_path = tmp_path / "poddown.toml"
    config_path.write_text('profile = "project-profile"\n', encoding="utf-8")

    completed = _run_preview(tmp_path, str(source_path), "--config", str(config_path))

    assert completed.returncode == 0
    assert completed.stderr == b""
    assert completed.stdout == (
        b"Profile: project-profile\n"
        + f"Source SHA-256: {hashlib.sha256(source).hexdigest()}\n".encode()
        + f"Source bytes: {len(source)}\n".encode()
        + b"Blocks: 2\n"
        + b"Provider calls: 0\n"
    )
    assert source_path.read_bytes() == source


def test_preview_cli_emits_sorted_compact_json_with_zero_provider_calls(tmp_path):
    """Catch JSON CLI output that is non-deterministic, padded, or path-dependent."""
    source = b"---\npoddown:\n  profile: document-profile\n---\n# Preview\n"
    source_path = _write_source(tmp_path, source)
    config_path = tmp_path / "profiles.toml"
    config_path.write_text('profiles = ["document-profile"]\n', encoding="utf-8")

    first = _run_preview(
        tmp_path, str(source_path), "--config", str(config_path), "--json"
    )
    second = _run_preview(
        tmp_path, str(source_path), "--config", str(config_path), "--json"
    )

    expected = {
        "block_count": 1,
        "profile_id": "document-profile",
        "provider_calls": 0,
        "source_bytes": len(source),
        "source_sha256": hashlib.sha256(source).hexdigest(),
    }
    expected_bytes = (
        json.dumps(
            expected, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        + b"\n"
    )
    assert first.returncode == second.returncode == 0
    assert first.stderr == second.stderr == b""
    assert first.stdout == second.stdout == expected_bytes


@pytest.mark.parametrize(
    ("source", "error_fragment"),
    [
        (b"# Invalid \xff\n", b"Source is not valid UTF-8"),
        (b"---\npoddown: [\n---\n# Broken\n", b"Invalid YAML frontmatter"),
        (
            b"---\npoddown:\n  profile: unknown-profile\n---\n# Unknown\n",
            b"Unknown profile: unknown-profile",
        ),
    ],
)
def test_preview_cli_maps_invalid_source_to_exit_code_two(
    tmp_path, source, error_fragment
):
    """Catch malformed UTF-8 or metadata returning a generic process failure."""
    source_path = _write_source(tmp_path, source)

    completed = _run_preview(tmp_path, str(source_path))

    assert completed.returncode == 2
    assert completed.stdout == b""
    assert error_fragment in completed.stderr


def test_preview_cli_resolves_toml_precedence_and_missing_config_fallback(tmp_path):
    """Catch TOML profile resolution skipping a higher-priority local source."""
    xdg_config = tmp_path / "xdg" / "poddown"
    xdg_config.mkdir(parents=True)
    (xdg_config / "config.toml").write_text(
        'profile = "user-profile"\n', encoding="utf-8"
    )
    project_config = tmp_path / "poddown.toml"
    project_config.write_text(
        'profile = "project-profile"\nprofiles = ["document-profile"]\n',
        encoding="utf-8",
    )
    document_path = _write_source(
        tmp_path,
        b"---\npoddown:\n  profile: document-profile\n---\n# Preview\n",
    )
    body_path = tmp_path / "body.md"
    body_path.write_bytes(b"# Preview\n")

    flag = _run_preview(
        tmp_path,
        str(document_path),
        "--config",
        str(project_config),
        "--profile",
        "flag-profile",
    )
    document = _run_preview(
        tmp_path, str(document_path), "--config", str(project_config)
    )
    project = _run_preview(tmp_path, str(body_path), "--config", str(project_config))
    user = _run_preview(
        tmp_path, str(body_path), "--config", str(tmp_path / "missing.toml")
    )
    (xdg_config / "config.toml").unlink()
    fallback = _run_preview(
        tmp_path, str(body_path), "--config", str(tmp_path / "missing.toml")
    )

    assert [item.returncode for item in (flag, document, project, user, fallback)] == [
        0,
        0,
        0,
        0,
        0,
    ]
    assert [
        item.stdout.splitlines()[0]
        for item in (flag, document, project, user, fallback)
    ] == [
        b"Profile: flag-profile",
        b"Profile: document-profile",
        b"Profile: project-profile",
        b"Profile: user-profile",
        b"Profile: default",
    ]


def test_preview_cli_rejects_malformed_toml_as_validation_error(tmp_path):
    """Catch a malformed local configuration crashing instead of returning code two."""
    source_path = _write_source(tmp_path, b"# Preview\n")
    config_path = tmp_path / "broken.toml"
    config_path.write_text("profile = [\n", encoding="utf-8")

    completed = _run_preview(tmp_path, str(source_path), "--config", str(config_path))

    assert completed.returncode == 2
    assert completed.stdout == b""
    assert b"Invalid configuration" in completed.stderr
