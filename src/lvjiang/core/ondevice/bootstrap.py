"""手机独立初始化：官方 system、默认用户和空 DB，不依赖 PC 同步。"""
from __future__ import annotations

import json
from pathlib import Path

from ... import constants
from ..config.interface import get_interface_store
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
            if not updated:
                # 每次新进程启动仍重建修订记录，手工修改可通过重启生效。
                reset_configuration()
            # 名册为空就不设当前用户（新实例不再自动造 default）；有用户时把
            # 「当前用户」按与界面相同的规则固化一次，之后无 UI 的读者
            # （任务启动、离线同步）拿到的就是同一个用户。
            manager = UserConfigManager()
            store = get_interface_store()
            if manager.list_users():
                active = manager.get_active_user_name()
                if active and store.get_active("user", "") != active:
                    store.set_active("user", active)
            elif store.get_active("user", ""):
                store.set_active("user", "")
            if not store.get_active("layout", ""):
                store.set_active("layout", "android")
            store.set_active("env", "android")
            from ..profile.repository import get_profile_db
            get_profile_db()  # 首次建表；不写任何虚构业务值。
            return json.dumps({"ok": True, "updated": updated}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"ok": False, "message": f"预置配置初始化失败：{exc}"}, ensure_ascii=False)
