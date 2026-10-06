"""设备端运行事件桥接；PC 测试环境不依赖 Java。"""
from __future__ import annotations

import json
import sys


def record(event: str, detail: str = "") -> None:
    if not hasattr(sys, "getandroidapilevel"):
        return
    try:
        from com.lvjiang.app import RuntimeDiagnostics
        RuntimeDiagnostics.INSTANCE.recordMessage(event, detail)
    except Exception:
        # 诊断不能掩盖原始执行失败，尤其不能在 OOM 处理期间再次抛错。
        pass


def memory_snapshot() -> dict:
    if not hasattr(sys, "getandroidapilevel"):
        return {}
    try:
        from com.lvjiang.app import RuntimeDiagnostics
        return json.loads(str(RuntimeDiagnostics.INSTANCE.snapshot()))
    except Exception:
        return {}
