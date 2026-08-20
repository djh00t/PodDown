"""Verify a clean checkout and the provider-free reference-demo invariants."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_ARTIFACTS = (
    "episode.wav",
    "episode.mp3",
    "transcript.txt",
    "transcript.vtt",
    "chapters.json",
    "show-notes.md",
    "qa-report.json",
    "provenance.json",
    "render-manifest.json",
)
_DETERMINISTIC_MODE = "deterministic-local-demo"
_HOST_LOCAL_MODE = "local-system-tts-demo"


class DemoVerificationError(ValueError):
    """The checkout or local demo evidence is not release-safe."""


def _mapping(value: object, *, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise DemoVerificationError(f"{field} must be a mapping")
    return value


def _string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise DemoVerificationError(f"{field} is missing")
    return value


def _string_sequence(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) for item in value
    ):
        raise DemoVerificationError(f"{field} is malformed")
    return tuple(value)


def _verify_local_demo_fields(
    value: Mapping[str, object],
    *,
    expected_mode: str,
    require_regeneration: bool = True,
) -> Mapping[str, object]:
    """Verify common offline demo evidence without accepting provider claims."""
    result = _mapping(value, field="demo result")
    if result.get("mode") != expected_mode:
        raise DemoVerificationError(f"mode is not {expected_mode}")
    for key in result:
        normalized = key.casefold()
        if "provider" in normalized or normalized in {"asr", "transcript_evidence"}:
            raise DemoVerificationError("provider evidence is not valid for demo")

    accuracy = result.get("critical_token_accuracy")
    if isinstance(accuracy, bool) or not isinstance(accuracy, (int, float)):
        raise DemoVerificationError("critical-token accuracy is malformed")
    if float(accuracy) != 1.0:
        raise DemoVerificationError("critical-token accuracy is not 100%")
    try:
        cost = Decimal(_string(result.get("cost"), field="cost"))
    except (InvalidOperation, ValueError) as error:
        raise DemoVerificationError("cost is malformed") from error
    if not cost.is_finite() or cost != 0:
        raise DemoVerificationError("demo cost is not zero")

    artifacts = _string_sequence(result.get("package_artifacts"), field="artifacts")
    if artifacts != _REQUIRED_ARTIFACTS:
        raise DemoVerificationError("package artifacts are incomplete")
    manifest = _string(result.get("package_manifest_sha256"), field="manifest")
    if _SHA256.fullmatch(manifest) is None:
        raise DemoVerificationError("manifest checksum is malformed")
    failed = _string_sequence(
        result.get("failed_segment_ids"),
        field="failed segments",
    )
    regenerated = _string_sequence(
        result.get("regenerated_segment_ids"),
        field="regenerated segments",
    )
    if require_regeneration and not failed:
        raise DemoVerificationError("failed-segment regeneration evidence is missing")
    if bool(failed) != bool(regenerated) or (
        failed and not set(failed).issubset(regenerated)
    ):
        raise DemoVerificationError("failed-segment regeneration evidence is missing")
    return result


def verify_demo_result(value: Mapping[str, object]) -> bool:
    """Verify deterministic demo evidence without accepting provider claims."""
    _verify_local_demo_fields(value, expected_mode=_DETERMINISTIC_MODE)
    return True


def _inspect_host_local_mp3(path: Path) -> Mapping[str, object]:
    """Inspect one host-local MP3 through the local FFprobe executable."""
    executable = shutil.which("ffprobe")
    if executable is None:
        raise DemoVerificationError("ffprobe is required for host-local media evidence")
    try:
        completed = subprocess.run(
            (
                executable,
                "-v",
                "error",
                "-show_entries",
                "stream=codec_name,sample_rate,channels,duration",
                "-of",
                "json",
                str(path),
            ),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DemoVerificationError(
            "ffprobe could not inspect host-local MP3"
        ) from error
    if completed.returncode != 0:
        raise DemoVerificationError("ffprobe rejected host-local MP3")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise DemoVerificationError(
            "ffprobe returned malformed media evidence"
        ) from error
    streams = payload.get("streams") if isinstance(payload, Mapping) else None
    if not isinstance(streams, list) or len(streams) != 1:
        raise DemoVerificationError("host-local MP3 must contain one audio stream")
    stream = _mapping(streams[0], field="host-local MP3 stream")
    return stream


def _validate_host_local_mp3(metadata: Mapping[str, object]) -> float:
    """Require a mono 44.1 kHz MP3 with a finite, non-zero duration."""
    if metadata.get("codec_name") != "mp3":
        raise DemoVerificationError("host-local media is not an MP3")
    if str(metadata.get("sample_rate")) != "44100":
        raise DemoVerificationError("host-local MP3 sample rate is not 44100 Hz")
    if metadata.get("channels") not in {1, "1"}:
        raise DemoVerificationError("host-local MP3 is not mono")
    try:
        duration = float(Decimal(str(metadata.get("duration"))))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise DemoVerificationError("host-local MP3 duration is malformed") from error
    if not math.isfinite(duration) or duration <= 0:
        raise DemoVerificationError("host-local MP3 duration is not positive")
    return duration


def verify_host_local_demo_result(
    value: Mapping[str, object],
    output: Path,
    *,
    mp3_inspector: Callable[[Path], Mapping[str, object]] | None = None,
) -> bool:
    """Verify host-local listening evidence and its published provenance."""
    _verify_local_demo_fields(
        value,
        expected_mode=_HOST_LOCAL_MODE,
        require_regeneration=False,
    )
    root = output.expanduser().resolve()
    if not root.is_dir():
        raise DemoVerificationError("host-local demo output is not a directory")
    published = root / "published"
    if not published.is_dir():
        raise DemoVerificationError("host-local published media is missing")
    mp3s = tuple(
        path
        for path in published.rglob("episode.mp3")
        if path.is_file() and not path.is_symlink()
    )
    if len(mp3s) != 1:
        raise DemoVerificationError("host-local output must contain one published MP3")
    inspector = mp3_inspector or _inspect_host_local_mp3
    try:
        duration = _validate_host_local_mp3(inspector(mp3s[0]))
    except DemoVerificationError:
        raise
    except (TypeError, ValueError) as error:
        raise DemoVerificationError("host-local media evidence is malformed") from error

    provenance_files = tuple(
        path
        for path in published.rglob("provenance.json")
        if path.is_file() and not path.is_symlink()
    )
    if len(provenance_files) != 1:
        raise DemoVerificationError("host-local provenance evidence is missing")
    try:
        provenance = _mapping(
            json.loads(provenance_files[0].read_text(encoding="utf-8")),
            field="host-local provenance",
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DemoVerificationError(
            "host-local provenance evidence is malformed"
        ) from error
    details = _mapping(provenance.get("details"), field="host-local provenance details")
    workflow = _mapping(details.get("workflow"), field="host-local workflow evidence")
    renderer = _mapping(workflow.get("renderer"), field="host-local renderer evidence")
    if (
        provenance.get("renderer") != "host-local-tts-v1"
        or provenance.get("qa") != "pass"
        or renderer.get("mode") != _HOST_LOCAL_MODE
        or renderer.get("provider") != "host-local"
    ):
        raise DemoVerificationError("host-local renderer provenance is invalid")
    if duration <= 0:  # Keep the duration part of the verified evidence path.
        raise DemoVerificationError("host-local MP3 duration is not positive")
    return True


def _run(
    command: Sequence[str], *, cwd: Path, env: Mapping[str, str] | None = None
) -> str:
    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            env=None if env is None else dict(env),
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise DemoVerificationError(f"command could not run: {command[0]}") from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise DemoVerificationError(
            f"command failed ({completed.returncode}): {command[0]} {detail}"
        )
    return completed.stdout


def _external_output(root: Path, output: Path) -> Path:
    resolved = output.expanduser().resolve()
    if resolved == root or root in resolved.parents:
        raise DemoVerificationError("demo output must be outside the checkout")
    return resolved


def verify_clean_checkout(
    root: Path,
    *,
    demo_output: Path | None = None,
    audio_mode: str = "deterministic",
) -> dict[str, object]:
    """Run clean-checkout checks and optionally a local demo/resume pair."""
    if audio_mode not in {"deterministic", "local-speech"}:
        raise DemoVerificationError("audio mode must be deterministic or local-speech")
    checkout = root.expanduser().resolve()
    top = Path(
        _run(("git", "rev-parse", "--show-toplevel"), cwd=checkout).strip()
    ).resolve()
    if top != checkout:
        raise DemoVerificationError("requested root is not the Git checkout root")
    status = _run(("git", "status", "--porcelain"), cwd=checkout)
    if status.strip():
        raise DemoVerificationError("checkout is not clean")
    required = (
        checkout / "pyproject.toml",
        checkout / "uv.lock",
        checkout / "integrations/reference-demo/v1/source.md",
        checkout / "integrations/reference-demo/v1/profile.yaml",
    )
    missing = [
        str(path.relative_to(checkout)) for path in required if not path.is_file()
    ]
    if missing:
        raise DemoVerificationError(f"required checkout files are missing: {missing}")
    _run((sys.executable, "-m", "compileall", "-q", "src", "tests"), cwd=checkout)
    report: dict[str, object] = {
        "clean": True,
        "demo": None,
        "audio_mode": audio_mode,
        "live_provider": False,
        "root": str(checkout),
    }
    if demo_output is None:
        return report

    output = _external_output(checkout, demo_output)
    environment = os.environ.copy()
    environment["PYDANTIC_DISABLE_PLUGINS"] = "1"
    environment["PYTHONPATH"] = str(checkout / "src")
    command = (
        sys.executable,
        "-m",
        "poddown.demo",
        "--audio-mode",
        audio_mode,
        "--output",
        str(output),
    )
    _run(command, cwd=checkout, env=environment)
    first = _mapping(
        json.loads((output / "result.json").read_text(encoding="utf-8")),
        field="demo result",
    )
    if audio_mode == "deterministic":
        verify_demo_result(first)
    else:
        verify_host_local_demo_result(first, output)
    first_manifest = first["package_manifest_sha256"]
    _run((*command, "--resume"), cwd=checkout, env=environment)
    resumed = _mapping(
        json.loads((output / "result.json").read_text(encoding="utf-8")),
        field="resumed demo result",
    )
    if audio_mode == "deterministic":
        verify_demo_result(resumed)
    else:
        verify_host_local_demo_result(resumed, output)
    if resumed.get("package_manifest_sha256") != first_manifest:
        raise DemoVerificationError("resume changed the immutable package manifest")
    replayed_takes = resumed.get("replayed_takes")
    if not isinstance(replayed_takes, int) or replayed_takes <= 0:
        raise DemoVerificationError("resume did not report replayed render evidence")
    report["demo"] = {
        "critical_token_accuracy": first["critical_token_accuracy"],
        "package_manifest_sha256": first_manifest,
        "replayed_takes": replayed_takes,
    }
    return report


def main(argv: Sequence[str] | None = None) -> int:
    """Verify a clean checkout and optional local demo output."""
    parser = argparse.ArgumentParser(prog="verify-clean-checkout")
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--demo-output", type=Path)
    parser.add_argument(
        "--audio-mode",
        choices=("deterministic", "local-speech"),
        default="deterministic",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = verify_clean_checkout(
            args.root,
            demo_output=args.demo_output,
            audio_mode=args.audio_mode,
        )
    except (DemoVerificationError, OSError, json.JSONDecodeError) as error:
        print(f"verification failed: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print("clean checkout verified")
        if report["demo"] is not None:
            print(f"{args.audio_mode} demo and resume verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
