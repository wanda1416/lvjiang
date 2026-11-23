"""Explicit task/fixture inventory; missing evidence never counts as green."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _fixture_dependencies(fixture: Path, spec: dict) -> dict:
    """Stdlib-only pin check, also usable before isolated dispatch."""
    if "workflow_bundle" not in spec:
        return {}
    directory = (fixture / spec["workflow_bundle"]).resolve()
    manifest = (directory / "manifest.json").resolve()
    if (
        not manifest.is_relative_to(fixture.resolve())
        or manifest.stat().st_size > 100_000
    ):
        raise ValueError("dependency manifest outside fixture/budget")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    return {key: value for key, value in data["files"].items() if key != data["entry"]}


def _source_snapshot(root: Path) -> dict:
    """Stdlib only: no product/config imports in the dispatching parent."""
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("source root missing; regenerate catalog")
    paths = sorted(root.rglob("*.wf"))
    if len(paths) > 5000:
        raise ValueError("source file count budget")
    result = {}
    for path in paths:
        relative = path.relative_to(root).as_posix()
        # Unavailable/unsafe source remains an unsupported inventory item, never read outside root.
        result[relative] = (
            hashlib.sha256(path.read_bytes()).hexdigest()
            if path.resolve().is_relative_to(root.resolve())
            and path.is_file()
            and path.stat().st_size <= 1_000_000
            else None
        )
    return result


def _source_error(data: dict) -> str:
    try:
        current = _source_snapshot(Path(data["source_root"]))
        if current != data["source_sha256"]:
            return "stale_source: workflow inventory/entry/dependency changed; regenerate catalog"
        for task in data["workflows"]:
            if task.get("sha256") and current.get(task["workflow"]) != task["sha256"]:
                return "stale_source: inconsistent catalog entry"
            for name, sha in task.get("dependency_sha256", {}).items():
                if current.get(name) != sha:
                    return "stale_source: inconsistent catalog dependency"
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return f"stale_source: {type(exc).__name__}; regenerate catalog"
    return ""


def discover(workflows: Path, fixtures: Path) -> dict:
    from ...workflows.grammar.parser import parse_file
    from .bundle import collect_workflow
    from .runner import _preflight
    from .scenario import Scenario

    workflows = workflows.resolve()
    workflow_files = sorted(workflows.rglob("*.wf"))
    fixture_files = sorted(fixtures.rglob("scenario.json")) if fixtures.exists() else []
    if len(workflow_files) > 5000 or len(fixture_files) > 5000:
        raise ValueError("catalog file count budget")
    pinned: dict[str, list[str]] = {}
    invalid = []
    fixture_specs = {}
    for path in fixture_files:
        try:
            case = Scenario.load(path.parent)
            fixture_name = str(path.parent.resolve())
            pinned.setdefault(case.spec["workflow_sha256"], []).append(fixture_name)
            fixture_specs[fixture_name] = case.spec
        except Exception as exc:
            invalid.append(
                {"fixture": str(path.parent.resolve()), "error": str(exc)[:512]}
            )
    rows = []
    for path in workflow_files:
        row: dict = {"workflow": path.relative_to(workflows).as_posix(), "kind": "task"}
        try:
            if not path.resolve().is_relative_to(workflows.resolve()):
                raise ValueError("workflow symlink outside root")
            if path.stat().st_size > 1_000_000:
                raise ValueError("workflow size budget")
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            row.update(sha256=sha, fixtures=pinned.get(sha, []))
            program = parse_file(path)
            row["kind"] = "task" if program.body else "library"
            entry_name, libraries = collect_workflow(path, roots=[workflows])
            expected = {
                key: hashlib.sha256(value.read_bytes()).hexdigest()
                for key, value in libraries.items()
                if key != entry_name
            }
            row["dependency_sha256"] = expected
            accepted, rejected = [], []
            for fixture_name in row["fixtures"]:
                if (
                    _fixture_dependencies(
                        Path(fixture_name), fixture_specs[fixture_name]
                    )
                    == expected
                ):
                    accepted.append(fixture_name)
                else:
                    rejected.append(
                        {
                            "fixture": fixture_name,
                            "reason": "dependency_pins_do_not_match_source",
                        }
                    )
            row.update(fixtures=accepted, rejected_fixtures=rejected)
            if accepted:
                supported_fixtures = []
                last_unsupported_reason = ""
                for fixture_name in accepted:
                    env_name = str(fixture_specs[fixture_name].get("run_env", "") or "")
                    try:
                        _preflight(path, list(libraries.values()), run_env=env_name)
                        supported_fixtures.append(fixture_name)
                    except Exception as exc:
                        last_unsupported_reason = str(exc)[:1024]
                if supported_fixtures:
                    row["fixtures"] = supported_fixtures
                    row["capability_status"] = "supported"
                else:
                    row.update(capability_status="unsupported", reason=last_unsupported_reason)
            else:
                _preflight(path, list(libraries.values()))
                row["capability_status"] = "supported"
        except Exception as exc:
            row.update(capability_status="unsupported", reason=str(exc)[:1024])
        row["coverage_status"] = (
            "library"
            if row["kind"] == "library"
            else "ready"
            if row.get("fixtures")
            else "stale_fixture"
            if row.get("rejected_fixtures")
            else "missing_recording"
        )
        rows.append(row)
    return {
        "format_version": 2,
        "scope": "explicit_workflow_root_only",
        "source_root": str(workflows),
        "source_sha256": _source_snapshot(workflows),
        "fixture_sha256": {
            name: hashlib.sha256(
                (Path(name) / "scenario.json").read_bytes()
            ).hexdigest()
            for name in fixture_specs
        },
        "workflows": rows,
        "invalid_fixtures": invalid,
        "provenance": "static_inventory_not_execution",
    }


def run_discovered(
    catalog: Path, output: Path, *, jobs: int = 2, timeout: float = 30
) -> dict:
    from .runner import run_cases

    if catalog.stat().st_size > 10_000_000:
        raise ValueError("catalog size budget")
    data = json.loads(catalog.read_text(encoding="utf-8"))
    if data.get("format_version") != 2:
        raise ValueError(
            "catalog requires current source identity; regenerate with catalog command"
        )
    source_error = _source_error(data)
    rows = []
    for task in data["workflows"]:
        if task["kind"] == "library":
            continue
        cases = task.get("fixtures", [])
        if cases and task.get("capability_status") == "unsupported":
            rows.append(
                {
                    "workflow": task["workflow"],
                    "status": "unsupported",
                    "reason": task.get("reason", ""),
                    "fixtures": cases,
                }
            )
            continue
        for rejected in task.get("rejected_fixtures", []):
            rows.append(
                {
                    "workflow": task["workflow"],
                    "status": "failed",
                    "reason": rejected["reason"],
                    "rejected_fixture": rejected["fixture"],
                }
            )
        if not cases and task.get("rejected_fixtures"):
            continue
        if not cases:
            rows.append(
                {
                    "workflow": task["workflow"],
                    "status": "missing_recording",
                    "capability_status": task.get("capability_status"),
                    "reason": task.get("reason", ""),
                }
            )
            continue
        for case in cases:
            rows.append(
                {
                    "workflow": task["workflow"],
                    "fixture": case,
                    "expected_sha256": task["sha256"],
                    "expected_dependencies": task.get("dependency_sha256", {}),
                }
            )
    runnable = [r for r in rows if "fixture" in r]
    # Re-check pins against catalog before dispatch; a stale catalog is not coverage.
    valid = []
    for row in runnable:
        try:
            if source_error:
                raise ValueError(source_error)
            spec_path = (Path(row["fixture"]) / "scenario.json").resolve()
            if (
                not spec_path.is_relative_to(Path(row["fixture"]).resolve())
                or spec_path.stat().st_size > 2_000_000
            ):
                raise ValueError("scenario outside fixture/budget")
            raw_spec = spec_path.read_bytes()
            if hashlib.sha256(raw_spec).hexdigest() != data["fixture_sha256"].get(
                row["fixture"]
            ):
                raise ValueError(
                    "stale_fixture: layout/params/environment/graph manifest changed; regenerate catalog"
                )
            spec = json.loads(raw_spec)
            if spec["workflow_sha256"] != row["expected_sha256"]:
                raise ValueError("catalog workflow pin changed")
            if (
                _fixture_dependencies(Path(row["fixture"]), spec)
                != row["expected_dependencies"]
            ):
                raise ValueError("catalog dependency pins changed")
            valid.append(row)
        except Exception as exc:
            row.update(status="failed", reason=str(exc)[:512])
    results = (
        run_cases(
            [Path(r["fixture"]) for r in valid], output, jobs=jobs, timeout=timeout
        )
        if valid
        else []
    )
    for row, result in zip(valid, results, strict=True):
        row.update(result)
    source_error = source_error or _source_error(data)
    if source_error:
        for row in rows:
            if row.get("status") == "passed":
                row.update(
                    fixture_status="passed", status="failed", reason=source_error
                )
    counts = {
        key: sum(r.get("status") == key for r in rows)
        for key in ("passed", "failed", "unsupported", "timeout", "missing_recording")
    }
    report = {
        "format_version": 1,
        "counts": counts,
        "source_error": source_error,
        "capability_counts": {
            status: sum(
                task.get("capability_status") == status
                for task in data["workflows"]
                if task["kind"] == "task"
            )
            for status in ("supported", "unsupported")
        },
        "rows": rows,
        "invalid_fixtures": data.get("invalid_fixtures", []),
        "all_required_passed": bool(rows)
        and all(r.get("status") == "passed" for r in rows)
        and not data.get("invalid_fixtures")
        and not source_error,
    }
    output.mkdir(parents=True, exist_ok=True)
    temporary = output / "coverage.json.part"
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    temporary.replace(output / "coverage.json")
    return report
