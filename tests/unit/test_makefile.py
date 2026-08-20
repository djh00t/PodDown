"""Regression coverage for distribution build isolation."""

from pathlib import Path


def test_build_target_clears_stale_distributions_before_building():
    """Prevent publish from including artifacts left by an earlier build."""
    makefile = (Path(__file__).parents[2] / "Makefile").read_text()
    build_recipe = makefile.split("build:\n", maxsplit=1)[1].split(
        "\n\ntest:", maxsplit=1
    )[0]

    assert build_recipe.index("rm -rf dist") < build_recipe.index("uv build")
