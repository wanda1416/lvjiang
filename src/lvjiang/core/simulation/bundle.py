"""Pin only explicitly resolved WF libraries, without Python or root escapes."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path, PurePosixPath

from .scenario import SimulationError, confined


def wf_name(raw: str) -> str:
    path = PurePosixPath(raw.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or ":" in raw or path.suffix != ".wf":
        raise SimulationError("unsupported_import_path", raw)
    return path.as_posix()


def collect_workflow(entry: Path, *, roots: list[Path]) -> tuple[str, dict[str, Path]]:
    from ...workflows.grammar.parser import parse_file

    roots = [root.resolve() for root in roots]
    entry = entry.resolve()
    if entry.suffix != ".wf":
        raise SimulationError("unsupported_import_path", "entry must be WF")
    entry_name = next(
        (
            entry.relative_to(root).as_posix()
            for root in roots
            if entry.is_relative_to(root)
        ),
        "task.wf",
    )
    files: dict[str, Path] = {}
    active: set[str] = set()
    total = 0

    def walk(name: str, path: Path):
        nonlocal total
        if name in active:
            raise SimulationError("import_cycle", name)
        if name in files:
            if files[name] != path:
                raise SimulationError("ambiguous_import", name)
            return
        if len(files) >= 512 or path.stat().st_size > 1_000_000:
            raise SimulationError("dependency_budget")
        total += path.stat().st_size
        if total > 8_000_000:
            raise SimulationError("dependency_budget")
        files[name] = path
        active.add(name)
        for item in parse_file(path).imports:
            reference = wf_name(item.path)
            found = next(
                (
                    candidate
                    for root in roots
                    if (candidate := confined(root, reference)).is_file()
                ),
                None,
            )
            if found is None:
                raise SimulationError("missing_dependency", reference)
            walk(reference, found)
        active.remove(name)

    walk(wf_name(entry_name), entry)
    return entry_name, files


def bundle_workflow(entry: Path, destination: Path, *, roots: list[Path]) -> dict:
    entry_name, files = collect_workflow(entry, roots=roots)
    destination.mkdir(parents=True, exist_ok=False)
    try:
        manifest: dict = {"format_version": 1, "entry": entry_name, "files": {}}
        for name, path in files.items():
            target = destination / "workflows" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            data = path.read_bytes()
            target.write_bytes(data)
            manifest["files"][name] = hashlib.sha256(data).hexdigest()
        (destination / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        return manifest
    except BaseException:
        shutil.rmtree(destination)
        raise


def verify_bundle(directory: Path) -> dict:
    manifest_path = confined(directory, "manifest.json")
    if manifest_path.stat().st_size > 100_000:
        raise SimulationError("dependency_budget")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format_version") != 1 or not 0 < len(manifest["files"]) <= 512:
        raise SimulationError("invalid_dependency_manifest")
    if manifest["entry"] not in manifest["files"]:
        raise SimulationError("missing_entry")
    total = 0
    for name, sha in manifest["files"].items():
        path = confined(directory / "workflows", wf_name(name))
        total += path.stat().st_size
        if path.stat().st_size > 1_000_000 or total > 8_000_000:
            raise SimulationError("dependency_budget")
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise SimulationError("dependency_hash_mismatch", name)
    # Every import must be pinned, even on unexecuted branches.
    from ...workflows.grammar.parser import parse_file

    active: set[str] = set()
    visited: set[str] = set()

    def walk(name):
        if name in active:
            raise SimulationError("import_cycle", name)
        if name in visited:
            return
        if name not in manifest["files"]:
            raise SimulationError("missing_dependency", name)
        active.add(name)
        for item in parse_file(directory / "workflows" / name).imports:
            walk(wf_name(item.path))
        active.remove(name)
        visited.add(name)

    walk(manifest["entry"])
    return manifest
