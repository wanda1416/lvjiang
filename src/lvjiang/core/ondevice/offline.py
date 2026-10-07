"""APK 同步应用与运行环境自检入口；所有写操作仅在手机任务空闲时进行。"""

from __future__ import annotations

import importlib
import json
import time
import traceback
from pathlib import Path

from ... import constants


def sync_status() -> dict:
    path = constants.SESSION_CONFIG_DIR / "offline.json"
    if not path.exists():
        return {"synced": False, "message": "尚未从 PC 同步离线任务配置"}
    try:
        return {"synced": True, **json.loads(path.read_text(encoding="utf-8"))}
    except (ValueError, OSError) as exc:
        return {"synced": False, "message": f"同步状态读取失败: {exc}"}


def configuration_status() -> dict:
    """运行配置就绪与同步历史分开；预置初始化不伪造 PC 同步记录。"""
    from ..config.session import get_session_store
    sync = sync_status()
    marker = constants.SESSION_CONFIG_DIR / "preset.json"
    system = constants.SESSION_CONFIG_DIR.parent / "system"
    preset_ready = marker.is_file() and all((system / name).is_file() for name in ("app.yaml", "ocr.yaml", "layouts.yaml"))
    ready = bool(sync.get("synced") or preset_ready)
    store = get_session_store()
    return {**sync, "ready": ready, "source": "pc" if sync.get("synced") else "preset",
            "execution_username": store.get_active("user", ""),
            "layout": store.get_active("layout", sync.get("layout", "")),
            "message": "使用应用预置配置" if preset_ready and not sync.get("synced") else
                sync.get("message", "") if ready else "预置配置未就绪，请重新打开或更新应用"}


def reset_configuration() -> None:
    from ...apps import load_app
    from ..config.resolver import get_resolver
    from ..config.session import reset_session_store
    from ..ocr_cleaner import OCRCleaner
    from ..profile.repository import reset_profile_db
    from ..profile.schema import reload_profile_config
    from ..recognizers.template_locator import get_template_store
    from ..scene_registry import reload_scene_registry
    from . import plugins, task_runner

    task_runner.release_engine()
    get_resolver().clear_read_cache()
    reset_session_store()
    reset_profile_db()
    reload_profile_config()
    reload_scene_registry()
    OCRCleaner.reset_instance()
    get_template_store().invalidate()
    for name in plugins.configured_apps():
        for callback in load_app(name).configuration_reload_callbacks:
            callback()


def apply_sync(path: str, preserve_task_params: bool = True) -> str:
    from ..offline_bundle import install_offline_bundle
    from .task_runner import CONTROL_LOCK, is_running

    with CONTROL_LOCK:
        if is_running():
            return json.dumps({"ok": False, "message": "请先结束手机任务再同步"}, ensure_ascii=False)
        try:
            summary = install_offline_bundle(Path(path), constants.PROJECT_ROOT,
                                             preserve_task_params=preserve_task_params, on_applied=reset_configuration)
            return json.dumps({"ok": True, "message": "脚本、配置与 DB 已同步到手机", "sync": summary}, ensure_ascii=False)
        except Exception as exc:
            # 安装器已恢复旧配置；同时丢弃重载失败前创建的部分缓存。
            try:
                reset_configuration()
            except Exception:
                pass
            return json.dumps({"ok": False, "message": f"同步失败: {exc}", "detail": traceback.format_exc()}, ensure_ascii=False)


def check_runtime(ocr: bool = True, screen_repetitions: int = 0) -> str:
    """在实际 APK 内加载完整运行链，不把 hello/import 成功当成环境可用。"""
    from .task_runner import CONTROL_LOCK, is_running, release_engine

    with CONTROL_LOCK:
        if is_running():
            return json.dumps({"ok": False, "message": "请先结束手机任务再检查运行环境"}, ensure_ascii=False)
        try:
            return _check_runtime(ocr, screen_repetitions)
        finally:
            # 自检占用的会话不应常驻；与同步重载共用显式释放路径。
            release_engine()


def _check_runtime(ocr: bool, screen_repetitions: int) -> str:
    stages = []
    from .diagnostics import memory_snapshot, record
    try:
        if not 0 <= screen_repetitions <= 10:
            raise ValueError("屏幕 OCR 验收次数必须在 0～10 之间")
        for name in ("fasteners", "numpy", "cv2", "PIL", "yaml", "loguru", "lark",
                     "lvjiang.core.profile.service", "lvjiang.core.ondevice.workflow_runner"):
            importlib.import_module(name)
        stages.append("运行依赖与 Profile 管线加载通过")
        if not configuration_status().get("ready"):
            return json.dumps({"ok": False, "message": "运行依赖就绪；预置配置尚未初始化",
                               "stages": stages, "sync": configuration_status(), "memory": memory_snapshot()}, ensure_ascii=False)
        from .plugins import ensure_loaded
        ensure_loaded()
        stages.append("设备端插件加载通过")
        from .task_runner import _get_engine, list_tasks
        engine = _get_engine()
        stages.append("工作流引擎装配通过")
        tasks = json.loads(list_tasks(require_ready=False))
        if not tasks.get("ok"):
            raise RuntimeError(tasks.get("error", "任务发现失败"))
        stages.append(f"发现 {len(tasks['tasks'])} 个安卓任务")
        if ocr:
            import cv2
            import numpy as np
            image = np.full((100, 600, 3), 255, dtype=np.uint8)
            cv2.putText(image, "LVJIANG 12345", (10, 65), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 3)
            results = engine._ocr.recognize(image)
            text = " ".join(result.text for result in results)
            if "12345" not in text:
                raise RuntimeError(f"设备端 OCR 实际推理未通过: {text!r}")
            stages.append("ONNX 模型加载与实际 OCR 推理通过")
        samples = []
        for index in range(screen_repetitions):
            started = time.monotonic()
            image = engine._capture.capture()
            if image is None:
                raise RuntimeError("无法获取真实截图，请检查无障碍与当前页面")
            results = engine._ocr.recognize(image)
            if not results:
                raise RuntimeError("真实截图未识别到文字，不能作为游戏 OCR 内存验收")
            sample = {"iteration": index + 1, "shape": list(image.shape), "texts": len(results),
                      "elapsed_ms": round((time.monotonic() - started) * 1000), "memory": memory_snapshot()}
            samples.append(sample)
            record("screen_ocr_sample", json.dumps(sample))
            del image, results
        if samples:
            stages.append(f"真实屏幕连续 OCR {len(samples)} 次通过")
        record("runtime_check_ok")
        return json.dumps({"ok": True, "message": "离线执行环境就绪", "stages": stages, "sync": configuration_status(),
                           "memory": memory_snapshot(), "screen_samples": samples}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "message": f"执行环境未就绪: {type(exc).__name__}: {exc}",
                           "detail": traceback.format_exc(), "stages": stages, "sync": configuration_status(),
                           "memory": memory_snapshot()}, ensure_ascii=False)
