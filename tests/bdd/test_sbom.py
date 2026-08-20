"""BDD bindings for deterministic lockfile SBOM generation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pytest_bdd import given, scenarios, then, when

from scripts.generate_sbom import build_sbom, serialize_sbom

scenarios("../features/sbom.feature")


_HASH_A = "a" * 64
_HASH_B = "b" * 64

LOCKFILE = f"""\
version = 1
revision = 3
requires-python = ">=3.12"

[[package]]
name = "demo-package"
version = "1.2.3"
source = {{ registry = "https://pypi.org/simple" }}
dependencies = [{{ name = "demo-dependency" }}]
sdist = {{ hash = "sha256:{_HASH_A}" }}

[[package]]
name = "demo-dependency"
version = "4.5.6"
source = {{ registry = "https://pypi.org/simple" }}
sdist = {{ hash = "sha256:{_HASH_B}" }}
"""


@given("a small authoritative uv lockfile")
def authoritative_lockfile(tmp_path: Path, context: Any) -> None:
    path = tmp_path / "uv.lock"
    path.write_text(LOCKFILE, encoding="utf-8")
    context.values["lock_path"] = path


@when("I generate the dependency SBOM twice")
def generate_sbom_twice(context: Any) -> None:
    document = build_sbom(context.values["lock_path"])
    context.values["first"] = serialize_sbom(document)
    context.values["second"] = serialize_sbom(build_sbom(context.values["lock_path"]))
    context.values["document"] = document


@then("the SBOMs are byte-identical and include dependency hashes")
def sbom_is_deterministic(context: Any) -> None:
    assert context.values["first"] == context.values["second"]
    document = context.values["document"]
    assert document["bomFormat"] == "CycloneDX"
    assert document["specVersion"] == "1.5"
    components = document["components"]
    assert len(components) == 2
    assert all(component["hashes"] for component in components)
    dependency_refs = document["dependencies"]
    demo = next(
        item for item in dependency_refs if item["ref"] == "pkg:pypi/demo-package@1.2.3"
    )
    assert demo["dependsOn"] == ["pkg:pypi/demo-dependency@4.5.6"]
