"""Explicit per-run host composition; independent of Qt and domain plugins."""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from loguru import logger

from .recording import FullTaskRecorder


def recording_scope(
    root: Path,
    *,
    task_run_id: str,
    batch_run_id: str = "",
    target_scope: str = "",
    provenance: str = "recorded",
):
    """Freeze identity at launch; scope invoked only in the task worker."""
    identity = (str(task_run_id), str(batch_run_id), str(target_scope))

    @contextmanager
    def scope(engine: Any, source: Any):
        # A launch option is one-shot even when the host reuses this engine.
        # Never carry the previous task identity into a later unrecorded execute.
        if getattr(engine, "execution_scope", None) is scope:
            engine.execution_scope = None
        task_id, batch_id, target = identity
        recorder = None
        originals = []
        old_observer = getattr(engine, "capture_observer", None)
        status = {
            "state": "initializing",
            "task_run_id": task_id,
            "batch_run_id": batch_id,
            "task_status": "running",
        }
        engine.full_capture_status = status
        try:
            try:
                directory = root / uuid.uuid4().hex
                workflow = Path(source) if isinstance(source, (str, Path)) else None
                layout_bytes = None
                context = {}
                if engine._layout is not None:
                    try:
                        layout_bytes = (
                            json.dumps(
                                engine._layout.to_dict(), ensure_ascii=False, indent=2
                            )
                            + "\n"
                        ).encode("utf-8")
                        if len(layout_bytes) > 2_000_000:
                            raise ValueError("layout budget")
                        context["layout_sha256"] = hashlib.sha256(
                            layout_bytes
                        ).hexdigest()
                    except Exception as exc:
                        layout_bytes = None
                        status["layout_status"] = f"unsupported:{type(exc).__name__}"
                recorder = FullTaskRecorder(
                    directory,
                    workflow=workflow,
                    task_run_id=task_id or None,
                    batch_run_id=batch_id or None,
                    provenance=provenance,
                    context=context,
                )
                if layout_bytes is not None:
                    (directory / "layout.json").write_bytes(layout_bytes)
                    status["layout_status"] = "ready"
                if workflow is not None:
                    try:
                        from ..config.resolver import get_resolver
                        from .bundle import bundle_workflow

                        resolver = get_resolver()
                        bundle_workflow(
                            workflow,
                            directory / "workflow_bundle",
                            roots=[
                                resolver.local_dir / "workflows",
                                resolver.system_dir / "workflows",
                            ],
                        )
                        status["bundle_status"] = "ready"
                    except Exception as exc:
                        status["bundle_status"] = f"unsupported:{type(exc).__name__}"
                recorder.account_artifacts(
                    sum(
                        path.stat().st_size
                        for path in directory.rglob("*")
                        if path.is_file() and path.name != "manifest.json"
                    )
                )
                status.update(
                    state="recording",
                    directory=str(directory),
                    target_scope_sha256=hashlib.sha256(
                        target.encode("utf-8")
                    ).hexdigest(),
                )
                capture = recorder.wrap_capture(engine._capture)
                input_ctrl = (
                    recorder.wrap_input(engine._input, capture)
                    if engine._input is not None
                    else None
                )
                observe = recorder.observer("e1", capture=capture)
                owners = [engine]
                if workflow is None:
                    owners.append(source)
                facade = getattr(engine, "_workflow", None)
                if facade is not None and all(facade is not owner for owner in owners):
                    owners.append(facade)
                for owner in owners:
                    for attribute, replacement in [
                        ("_capture", capture),
                        ("_input", input_ctrl),
                    ]:
                        originals.append((owner, attribute, getattr(owner, attribute)))
                        setattr(owner, attribute, replacement)

                registry = getattr(engine, "_key_registry", None)
                if registry is not None:
                    # press caches its backend independently of engine._input.
                    registry._backend = input_ctrl

                def observers(meta, image):
                    observe(meta, image)
                    if old_observer is not None:
                        old_observer(meta, image)

                engine.capture_observer = observers
                if workflow is None:
                    originals.append(
                        (
                            source,
                            "capture_observer",
                            getattr(source, "capture_observer", None),
                        )
                    )
                    source.capture_observer = observe
                logger.info(
                    "全任务采集已开启: task_run_id={}, directory={}", task_id, directory
                )
                recorder.event(
                    "run_identity",
                    task_run_id=task_id,
                    batch_run_id=batch_id,
                    target_scope_sha256=status["target_scope_sha256"],
                )
            except Exception as exc:
                status.update(state="incomplete", reason=f"setup:{type(exc).__name__}")
                logger.warning("全任务采集启动失败，不影响任务: {}", type(exc).__name__)
            yield
            from ...workflows.result_protocol import return_failure_message

            status["task_status"] = (
                "failed" if return_failure_message(engine.return_value) else "completed"
            )
        except BaseException:
            status["task_status"] = "failed"
            raise
        finally:
            try:
                if engine._stop_check():
                    status["task_status"] = "cancelled"
            except Exception:
                # Diagnostics must not mask a business exception/return.
                pass
            registry = getattr(engine, "_key_registry", None)
            if registry is not None:
                # execute normally did this already; keep held-key cleanup inside
                # the recording also for a directly composed host scope.
                registry.release_all()
            for owner, attribute, original in reversed(originals):
                setattr(owner, attribute, original)
            if registry is not None:
                # Includes a registry first created while the wrapper was installed.
                registry._backend = engine._input
            engine.capture_observer = old_observer
            facade = getattr(engine, "_workflow", None)
            if facade is not None and all(
                owner is not facade for owner, _, _ in originals
            ):
                # A lazy facade created while wrapped must not retain the closed recorder.
                facade._capture, facade._input = engine._capture, engine._input
            if recorder is not None:
                try:
                    recorder.task_status = status["task_status"]
                    recorder.event("task_result", status=status["task_status"])
                    recorder.close()
                    if status["state"] != "incomplete":
                        status.update(
                            state="incomplete"
                            if recorder.status["reason"]
                            else "complete",
                            reason=recorder.status["reason"],
                        )
                except Exception as exc:
                    status.update(
                        state="incomplete", reason=f"close:{type(exc).__name__}"
                    )
                    logger.warning(
                        "全任务采集收尾失败，不影响任务: {}", type(exc).__name__
                    )

            if status["state"] == "incomplete":
                logger.warning(
                    "全任务采集不完整: task_run_id={}, reason={}",
                    task_id,
                    status.get("reason", ""),
                )
            else:
                logger.info(
                    "全任务采集封口: task_run_id={}, directory={}, task_status={}",
                    task_id,
                    status.get("directory", ""),
                    status["task_status"],
                )

    return scope
