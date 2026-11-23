"""Isolated subprocess composition root; no runtime/config imports at module load."""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import random
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any


def _guard(
    runtime: Path,
    fixture: Path,
    *,
    read_additional: tuple[Path, ...] = (),
    write_additional: tuple[Path, ...] = (),
) -> None:
    """Accidental-I/O guard for trusted WF, NOT an OS sandbox for hostile code.

    Python/script imports and unmodelled builtin capabilities are rejected by
    preflight as well. No socket/device subprocess is available to the worker.
    """
    source = Path(__file__).resolve().parents[3]
    read_roots = [
        *read_additional,
        *write_additional,
        runtime,
        fixture,
        source,
        Path(sys.prefix).resolve(),
        Path(sys.base_prefix).resolve(),
        Path("/usr"),
        Path("/lib"),
        Path("/proc"),
        Path("/etc"),
        Path("/sys"),
    ]

    def path_allowed(raw: Any, writing: bool = False) -> bool:
        if isinstance(raw, int) or raw is None:
            return True
        try:
            path = Path(os.fsdecode(raw)).resolve()
        except (TypeError, ValueError):
            return False
        return any(
            path.is_relative_to(p)
            for p in ([runtime, *write_additional] if writing else read_roots)
        )

    def audit(event: str, args: tuple) -> None:
        if event.startswith("socket.") or event in {
            "subprocess.Popen",
            "os.system",
            "os.exec",
            "os.posix_spawn",
            "os.fork",
        }:
            raise PermissionError(f"offline worker forbids {event}")
        if event == "open":
            path, mode, flags = args
            writing = bool(
                (flags or 0)
                & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
            )
            writing = writing or bool(mode and any(c in mode for c in "wax+"))
            if not path_allowed(path, writing):
                raise PermissionError(
                    "offline worker file access outside permitted roots"
                )
        elif event in {
            "os.mkdir",
            "os.remove",
            "os.rmdir",
            "os.chmod",
            "os.truncate",
            "os.utime",
        }:
            if not path_allowed(args[0], True):
                raise PermissionError("offline worker write outside runtime")
        elif event in {"os.rename", "os.link", "os.symlink"}:
            if not all(path_allowed(p, True) for p in args[:2]):
                raise PermissionError("offline worker link/rename outside runtime")
        elif event == "sqlite3.connect" and str(args[0]) != ":memory:":
            if not path_allowed(args[0], True):
                raise PermissionError("offline worker database outside runtime")
        elif event in {"os.listdir", "os.scandir"} and not path_allowed(args[0]):
            raise PermissionError("offline worker directory outside permitted roots")

    sys.addaudithook(audit)


def _preflight(
    workflow: Path,
    libraries: list[Path] | None = None,
    *,
    run_env: str = "",
    require_environment: bool = False,
) -> None:
    from ...workflows.grammar.parser import parse_file
    from ...workflows.workflow_references import reachable_procs
    from .policy import PURE_FUNCTIONS, UNSUPPORTED_NODES
    from .scenario import SimulationError

    programs = [parse_file(path) for path in (libraries or [])] + [parse_file(workflow)]
    program = programs[-1]
    procs = {name: proc for item in programs for name, proc in item.procs.items()}

    def walk(value: Any) -> None:
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            kind = type(value).__name__
            if kind in UNSUPPORTED_NODES:
                raise SimulationError("unsupported_capability", kind)
            if (
                require_environment
                and kind in {"FuncCall", "Eval"}
                and value.func_name in {"env", "check_env"}
                and not run_env
            ):
                raise SimulationError(
                    "unsupported_environment", "fixture must declare run_env"
                )
            internal_eval = kind == "Eval" and value.func_name in {
                "__expr__",
                "__literal__",
                "__empty_dict__",
                "__dict__",
                "__list__",
                "__range__",
                "__tuple__",
                "__default__",
                "__arith__",
            }
            if (
                kind in {"FuncCall", "Eval"}
                and value.func_name not in PURE_FUNCTIONS
                and not internal_eval
            ):
                raise SimulationError("unsupported_capability", value.func_name)
            if kind == "CallProc" and value.name not in procs:
                raise SimulationError("unsupported_capability", value.name)
            if kind == "Recognize" and value.with_func is not None:
                raise SimulationError("unsupported_capability", "recognize transform")
            for field in dataclasses.fields(value):
                walk(getattr(value, field.name))
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (tuple, list)):
            for item in value:
                walk(item)

    walk(program.body)
    for name in reachable_procs(program.body, procs, run_env):
        walk(procs[name])


def _worker(fixture: Path, directory: Path, *, record: bool = False) -> int:
    # Before importing constants, configuration, OCR, plugins, or engine.
    runtime = directory / "runtime"
    runtime.mkdir()
    for name in (
        "LVJIANG_ROOT",
        "HOME",
        "USERPROFILE",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "TMPDIR",
        "TEMP",
        "TMP",
    ):
        os.environ[name] = str(runtime)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    os.chdir(runtime)
    _guard(directory, fixture)
    ocr = device = recorder = None
    report: dict[str, Any] = {"fixture": str(fixture), "status": "failed"}
    try:
        from loguru import logger

        from ...workflows.engine import WorkflowEngine
        from ...workflows.result_protocol import return_failure_message
        from ..config.models import InputSimConfig
        from ..layout.models import Layout
        from ..ocr.engine import OCREngine
        from .scenario import Scenario, SimulatedDevice, SimulationError

        logger.remove()
        logger.add(
            directory / "workflow.log", rotation="1 MB", retention=2, encoding="utf-8"
        )
        scenario = Scenario.load(fixture)
        execution_path = scenario.workflow
        libraries = []
        if "workflow_bundle" in scenario.spec:
            import shutil

            from .bundle import verify_bundle

            bundle_dir = fixture / scenario.spec["workflow_bundle"]
            bundle = verify_bundle(bundle_dir)
            library_root = runtime / "config" / "system" / "workflows"
            for name in bundle["files"]:
                target = library_root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(bundle_dir / "workflows" / name, target)
                libraries.append(target)
            execution_path = library_root / bundle["entry"]
        _preflight(
            execution_path,
            libraries,
            run_env=scenario.spec.get("run_env", ""),
            require_environment=True,
        )
        random.seed(0)  # Per-process only; no shared random state between cases.
        ocr = OCREngine(
            rapidocr_options={"intra_op_num_threads": 1, "inter_op_num_threads": 1}
        )
        with (directory / "events.jsonl").open("w", encoding="utf-8") as trace:
            device = SimulatedDevice(scenario, trace)
            capture: Any = device
            input_ctrl: Any = device
            observer = None
            if record:
                from .recording import FullTaskRecorder

                recorder = FullTaskRecorder(
                    directory / "recording",
                    workflow=scenario.workflow,
                    provenance=scenario.spec["provenance"],
                )
                if "workflow_bundle" in scenario.spec:
                    import shutil

                    shutil.copytree(
                        fixture / scenario.spec["workflow_bundle"],
                        directory / "recording" / "workflow_bundle",
                    )
                recorder.account_artifacts(
                    sum(
                        path.stat().st_size
                        for path in (directory / "recording").rglob("*")
                        if path.is_file() and path.name != "manifest.json"
                    )
                )
                capture = recorder.wrap_capture(device)
                input_ctrl = recorder.wrap_input(device, capture)
                observer = recorder.observer("e1", capture=capture)
            engine = WorkflowEngine(
                capture=capture,
                input_ctrl=input_ctrl,
                capture_observer=observer,
                ocr=ocr,
                layout=Layout.from_dict("simulation", scenario.spec.get("layout", {})),
                input_sim=InputSimConfig(
                    click_random_offset=0,
                    before_click_wait=(0, 0),
                    after_click_wait=(0, 0),
                ),
                runtime_clock=device,
                run_env=scenario.spec.get("run_env", ""),
                unattended=True,
                stop_check=lambda: device.stopped,
            )
            engine.users_dir = runtime / "config" / "session" / "users"
            environment = scenario.spec.get("environment", {})
            engine._delay_params = environment.get("delay_params", {})
            engine.session = environment.get("session", {})

            class DeclaredFields(dict):
                def get(self, key, default=None):
                    if key not in self:
                        device.fail(
                            "unsupported_environment",
                            "attribute field not declared; use explicit null for known absence",
                        )
                    return super().get(key, default)

            class DeclaredAttributes(dict):
                def __contains__(self, key):
                    if not super().__contains__(key):
                        device.fail(
                            "unsupported_environment", "attribute user not declared"
                        )
                    return True

            engine.user_attributes_snapshot = DeclaredAttributes(
                {
                    key: DeclaredFields(value)
                    for key, value in environment.get("attributes", {}).items()
                }
            )
            engine.unattended = environment.get("unattended", True)
            engine.statement_hook = lambda line, _variables: device.event(
                "statement", line=line
            )
            engine._ui_callback = lambda *_a, **_k: device.fail(
                "unsupported_interaction"
            )
            output = engine.execute(
                execution_path, initial_variables=scenario.spec.get("params", {})
            )
            device.assert_finished()
            failure = return_failure_message(engine.return_value)
            if failure:
                raise SimulationError("business_failure", failure)
            if (
                "expected_return" in scenario.spec
                and engine.return_value != scenario.spec["expected_return"]
            ):
                raise SimulationError("unexpected_return", str(engine.return_value))
            # Do not dump arbitrary plugin state/variables into the report.
            report.update(
                status="passed",
                final_state=device.state,
                virtual_seconds=device.time,
                events=device.events,
                return_value=engine.return_value,
                output_keys=list(output)[:32],
                provenance=scenario.spec["provenance"],
                visited_states=sorted(device.visited_states),
                visited_edges=sorted(device.visited_edges),
                declared_edges=len(scenario.spec["edges"]),
                coverage_scope="declared_fixture_only",
            )
    except Exception as exc:
        code = getattr(exc, "code", "runtime_error")
        if device is not None and device.failure is not None:
            code = device.failure.code
        report.update(
            status="unsupported" if str(code).startswith("unsupported") else "failed",
            code=code,
            error=str(exc)[:2048],
        )
        if device is not None:
            report.update(
                final_state=device.state,
                virtual_seconds=device.time,
                events=device.events,
            )
    finally:
        if recorder is not None:
            recorder.task_status = (
                "completed" if report["status"] == "passed" else "failed"
            )
            recorder.event("task_result", status=report["status"])
            recorder.close()
            report["recording_complete"] = not bool(recorder.status["reason"])
        if ocr is not None:
            ocr.close()
    temporary = directory / "result.json.part"
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, directory / "result.json")
    return 0 if report["status"] == "passed" else 1


def run_cases(
    fixtures: list[Path],
    output: Path,
    *,
    jobs: int = 2,
    timeout: float = 30,
    record: bool = False,
) -> list[dict]:
    """Each case is a fresh interpreter and root; a watchdog kills hung cases."""
    if (
        type(jobs) is not int
        or not 1 <= jobs <= 32
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ValueError("jobs must be 1..32 and timeout must be positive")
    if not fixtures:
        raise ValueError(
            "at least one explicit fixture is required; empty selection is not a pass"
        )
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    def run(item: tuple[int, Path]) -> dict:
        index, fixture = item
        fixture = fixture.resolve()
        directory = output / f"case-{index:04d}-{uuid.uuid4().hex[:8]}"
        directory.mkdir()
        # Never inherit model keys, account tokens, or other unrelated credentials.
        env = {
            k: os.environ[k]
            for k in ("PATH", "SYSTEMROOT", "WINDIR", "LANG")
            if k in os.environ
        }
        env.update(
            PYTHONIOENCODING="utf-8",
            PYTHONDONTWRITEBYTECODE="1",
            QT_QPA_PLATFORM="offscreen",
            PYTHONPATH=str(Path(__file__).resolve().parents[3]),
        )
        with (directory / "worker.log").open("wb") as log:
            try:
                process = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        "-m",
                        "lvjiang.core.simulation",
                        "_worker",
                        str(fixture),
                        str(directory),
                        *(["--record"] if record else []),
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=env,
                    timeout=timeout,
                    check=False,
                )
                path = directory / "result.json"
                report = (
                    json.loads(path.read_text(encoding="utf-8"))
                    if path.exists()
                    else {
                        "status": "failed",
                        "code": "worker_exit_without_result",
                        "exit_code": process.returncode,
                    }
                )
                if process.returncode and report.get("status") == "passed":
                    report = {"status": "failed", "code": "worker_exit_mismatch"}
            except subprocess.TimeoutExpired:
                report = {"status": "timeout", "code": "wall_timeout"}
                (directory / "result.json").write_text(
                    json.dumps(report) + "\n", encoding="utf-8"
                )
        return {**report, "case_directory": str(directory)}

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(run, enumerate(fixtures)))
    report_path = output / f"run-{uuid.uuid4().hex[:8]}.json"
    report_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="动作驱动的离线WF模拟器；未知分支不是通过"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("fixtures", type=Path, nargs="+")
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--jobs", type=int, default=2)
    run.add_argument("--timeout", type=float, default=30)
    run.add_argument(
        "--record",
        action="store_true",
        help="显式全任务采集；保留原fixture的synthetic/recorded来源",
    )
    worker = commands.add_parser("_worker", help=argparse.SUPPRESS)
    worker.add_argument("fixture", type=Path)
    worker.add_argument("directory", type=Path)
    worker.add_argument("--record", action="store_true")
    demo = commands.add_parser("demo")
    demo.add_argument("directory", type=Path)
    compile_cmd = commands.add_parser("compile")
    compile_cmd.add_argument("recording", type=Path)
    compile_cmd.add_argument("destination", type=Path)
    compile_cmd.add_argument("--workflow", type=Path, required=True)
    compile_cmd.add_argument("--terminal", action="append", required=True)
    compile_cmd.add_argument("--annotations", type=Path)
    compile_cmd.add_argument(
        "--context",
        type=Path,
        help="Reviewed fixture JSON: layout/params/environment/run_env, never real credentials",
    )
    annotate = commands.add_parser("annotation-template")
    annotate.add_argument("recording", type=Path)
    annotate.add_argument("destination", type=Path)
    pack = commands.add_parser("package")
    pack.add_argument("workflow", type=Path)
    pack.add_argument("destination", type=Path)
    pack.add_argument("--root", type=Path, action="append", required=True)
    catalog = commands.add_parser("catalog")
    catalog.add_argument("--workflows-root", type=Path, required=True)
    catalog.add_argument("--fixtures-root", type=Path, required=True)
    catalog.add_argument("--output", type=Path, required=True)
    discovered = commands.add_parser("run-discovered")
    discovered.add_argument("catalog", type=Path)
    discovered.add_argument("--output", type=Path, required=True)
    discovered.add_argument("--jobs", type=int, default=2)
    discovered.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args(argv)
    if args.command in {"package", "compile", "annotation-template", "catalog"}:
        import tempfile

        # Resolve explicit arguments before changing working directory; no credentials/config discovery.
        reads = []
        for name, value in vars(args).copy().items():
            if isinstance(value, Path):
                resolved = value.resolve()
                setattr(args, name, resolved)
                reads.append(resolved if resolved.is_dir() else resolved.parent)
            elif name == "root" and value:
                args.root = [path.resolve() for path in value]
                reads.extend(args.root)
        output = getattr(args, "output", None) or args.destination
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="lvjiang-wf-tool-")
        runtime = Path(temporary.name).resolve()
        for name in (
            "LVJIANG_ROOT",
            "HOME",
            "USERPROFILE",
            "XDG_CACHE_HOME",
            "XDG_CONFIG_HOME",
            "TMPDIR",
            "TEMP",
            "TMP",
        ):
            os.environ[name] = str(runtime)
        sys.dont_write_bytecode = True
        # Keep the TemporaryDirectory alive for this CLI invocation; audit prevents root-outside writes.
        _guard(
            runtime, runtime, read_additional=tuple(reads), write_additional=(output,)
        )
        os.chdir(runtime)

    if args.command == "_worker":
        return _worker(
            args.fixture.resolve(), args.directory.resolve(), record=args.record
        )
    if args.command == "demo":
        from .demo import make_demo

        make_demo(args.directory)
        return 0
    if args.command == "catalog":
        from .catalog import discover

        data = discover(args.workflows_root, args.fixtures_root)
        args.output.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return 0
    if args.command == "run-discovered":
        from .catalog import run_discovered

        report = run_discovered(
            args.catalog, args.output, jobs=args.jobs, timeout=args.timeout
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["all_required_passed"] else 1
    if args.command == "package":
        from .bundle import bundle_workflow

        bundle_workflow(args.workflow, args.destination, roots=args.root)
        return 0
    if args.command == "annotation-template":
        from .recording import annotation_template

        annotation_template(args.recording, args.destination)
        return 0
    if args.command == "compile":
        from .recording import compile_tape

        compile_tape(
            args.recording,
            args.destination,
            workflow=args.workflow,
            terminal=args.terminal,
            annotations=args.annotations,
            fixture_context=args.context,
        )
        return 0
    results = run_cases(
        args.fixtures,
        args.output,
        jobs=args.jobs,
        timeout=args.timeout,
        record=args.record,
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    succeeded = all(r["status"] == "passed" for r in results)
    if args.record:
        succeeded = succeeded and all(
            r.get("recording_complete") is True for r in results
        )
    return 0 if succeeded else 1
