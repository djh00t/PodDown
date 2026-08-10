"""Regression coverage for distribution build isolation."""

from pathlib import Path


def test_build_target_clears_stale_distributions_before_building():
    """Prevent publish from including artifacts left by an earlier build."""
    makefile = (Path(__file__).parents[2] / "Makefile").read_text()
    build_recipe = makefile.split("build:\n", maxsplit=1)[1].split(
        "\n\ntest:", maxsplit=1
    )[0]

    assert build_recipe.index("rm -rf dist") < build_recipe.index("uv build")


def test_demo_make_and_operator_docs_distinguish_local_and_live_audio_modes():
    """Keep the reference-demo's no-key local and explicit live boundaries clear."""
    root = Path(__file__).parents[2]
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    verification = (root / "docs/verification/reference-demo.md").read_text(
        encoding="utf-8"
    )
    providers = (root / "docs/provider-guidelines.md").read_text(encoding="utf-8")

    assert "--audio-mode local-speech" in makefile
    assert "local-system-tts-demo" in readme
    assert "deterministic-local-demo" in readme
    assert "--audio-mode deterministic" in readme
    assert "ffmpeg" in verification
    assert "ffprobe" in verification
    assert "say" in verification
    assert "espeak-ng" in verification
    assert "command -v" in verification
    assert "afinfo" in verification
    assert "ELEVENLABS_API_KEY" in providers
    assert "spending authorization" in providers
