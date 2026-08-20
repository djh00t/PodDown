"""Generate a deterministic CycloneDX SBOM from the uv lockfile."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_NAME = re.compile(r"[-_.]+")
_ROOT_SOURCE = "editable"


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value


def _normalized_name(value: str) -> str:
    return _NAME.sub("-", value).casefold()


def _purl(name: str, version: str) -> str:
    return f"pkg:pypi/{_normalized_name(name)}@{version}"


def _hashes(package: Mapping[str, object], name: str) -> list[str]:
    values: list[object] = []
    for field in ("sdist", "wheels"):
        raw = package.get(field)
        if isinstance(raw, Mapping):
            values.append(raw)
        elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            values.extend(raw)
    hashes: set[str] = set()
    for value in values:
        if not isinstance(value, Mapping):
            raise ValueError(f"{name} has malformed artifact metadata")
        raw_hash = value.get("hash")
        if not isinstance(raw_hash, str) or not raw_hash.startswith("sha256:"):
            raise ValueError(f"{name} is missing a SHA-256 artifact hash")
        digest = raw_hash.removeprefix("sha256:")
        if _SHA256.fullmatch(digest) is None:
            raise ValueError(f"{name} has an invalid SHA-256 artifact hash")
        hashes.add(digest)
    if not hashes:
        raise ValueError(f"{name} has no locked artifact hash")
    return sorted(hashes)


def _dependencies(package: Mapping[str, object], name: str) -> list[str]:
    raw = package.get("dependencies", [])
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError(f"{name} has malformed dependency metadata")
    names: list[str] = []
    for value in raw:
        if not isinstance(value, Mapping):
            raise ValueError(f"{name} has malformed dependency metadata")
        names.append(_normalized_name(_text(value.get("name"), field="dependency")))
    return sorted(set(names))


def _package_records(lock: Mapping[str, object]) -> list[Mapping[str, object]]:
    raw = lock.get("package")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError("uv lockfile has no package records")
    records: list[Mapping[str, object]] = []
    for value in raw:
        if not isinstance(value, Mapping):
            raise ValueError("uv lockfile contains a malformed package record")
        records.append(value)
    if not records:
        raise ValueError("uv lockfile has no package records")
    return records


def build_sbom(lock_path: Path) -> dict[str, object]:
    """Build a reproducible CycloneDX 1.5 document from one uv lockfile."""
    try:
        lock_bytes = lock_path.read_bytes()
        lock = tomllib.loads(lock_bytes.decode("utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError("uv lockfile could not be read") from error
    if not isinstance(lock, Mapping):
        raise ValueError("uv lockfile must contain a mapping")

    records = _package_records(lock)
    components: list[dict[str, object]] = []
    refs_by_name: dict[str, str] = {}
    dependencies_by_ref: dict[str, list[str]] = {}
    root_ref: str | None = None
    root_dependencies: list[str] = []
    for package in records:
        name = _text(package.get("name"), field="package name")
        version = _text(package.get("version"), field=f"{name} version")
        normalized = _normalized_name(name)
        source = package.get("source")
        is_root = isinstance(source, Mapping) and _ROOT_SOURCE in source
        ref = _purl(name, version)
        if is_root:
            if root_ref is not None:
                raise ValueError("uv lockfile contains multiple editable roots")
            root_ref = ref
            root_dependencies = _dependencies(package, name)
            continue
        if normalized in refs_by_name and refs_by_name[normalized] != ref:
            raise ValueError(f"uv lockfile contains conflicting versions for {name}")
        refs_by_name[normalized] = ref
        components.append(
            {
                "bom-ref": ref,
                "hashes": [
                    {"alg": "SHA-256", "content": digest}
                    for digest in _hashes(package, name)
                ],
                "name": name,
                "purl": ref,
                "type": "library",
                "version": version,
            }
        )
        dependencies_by_ref[ref] = _dependencies(package, name)

    dependencies: list[dict[str, object]] = []
    if root_ref is not None:
        dependencies.append(
            {
                "dependsOn": [refs_by_name[name] for name in root_dependencies],
                "ref": root_ref,
            }
        )
    for ref in sorted(dependencies_by_ref):
        dependencies.append(
            {
                "dependsOn": [
                    refs_by_name[name]
                    for name in dependencies_by_ref[ref]
                    if name in refs_by_name
                ],
                "ref": ref,
            }
        )

    metadata: dict[str, object] = {
        "properties": [
            {
                "name": "poddown:uv-lock-sha256",
                "value": sha256(lock_bytes).hexdigest(),
            }
        ]
    }
    if root_ref is not None:
        metadata["component"] = {
            "bom-ref": root_ref,
            "name": next(
                _text(package.get("name"), field="package name")
                for package in records
                if isinstance(package.get("source"), Mapping)
                and _ROOT_SOURCE in package["source"]
            ),
            "purl": root_ref,
            "type": "application",
            "version": root_ref.rsplit("@", 1)[1],
        }

    serial = uuid5(NAMESPACE_URL, sha256(lock_bytes).hexdigest())
    return {
        "bomFormat": "CycloneDX",
        "components": sorted(components, key=lambda item: str(item["bom-ref"])),
        "dependencies": dependencies,
        "metadata": metadata,
        "serialNumber": f"urn:uuid:{serial}",
        "specVersion": "1.5",
        "version": 1,
    }


def serialize_sbom(document: Mapping[str, object]) -> bytes:
    """Serialize an SBOM without timestamps or environment-specific fields."""
    return (
        json.dumps(document, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def write_sbom(lock_path: Path, output_path: Path) -> None:
    """Generate and write one deterministic SBOM artifact."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(serialize_sbom(build_sbom(lock_path)))


def main(argv: Sequence[str] | None = None) -> int:
    """Run the lockfile SBOM generator."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=Path("uv.lock"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        write_sbom(args.lock, args.output)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
