"""手机独立初始化：官方 system、默认用户和空 DB，不依赖 PC 同步。"""
from __future__ import annotations

import json
from pathlib import Path

from ... import constants
from ..config.session import get_session_store
from ..system_preset import install_system_preset
from ..user_config import UserConfigManager
from .offline import reset_configuration
from .task_runner import CONTROL_LOCK, is_running


def initialize(preset_path: str) -> str:
    with CONTROL_LOCK:
        try:
            if is_running():
                raise ValueError("请先结束任务再初始化配置")
            updated = install_system_preset(Path(preset_path), constants.PROJECT_ROOT, on_applied=reset_configuration)
            manager = UserConfigManager()
            store = get_session_store()
            if not store.get_active("user", ""):
                store.set_active("user", manager.list_users()[0])
            if not store.get_active("layout", ""):
                store.set_active("layout", "android")
            store.update_node("settings", {"env": "android"})
            from ..profile.repository import get_profile_db
            get_profile_db()  # 首次建表；不写任何虚构业务值。
            return json.dumps({"ok": True, "updated": updated}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"ok": False, "message": f"预置配置初始化失败：{exc}"}, ensure_ascii=False)
