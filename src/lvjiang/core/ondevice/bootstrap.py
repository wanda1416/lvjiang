"""手机独立初始化：官方 system、默认用户和空 DB，不依赖 PC 同步。"""
from __future__ import annotations

import json
from pathlib import Path

from ... import constants
from ..config.interface import get_interface_store
from ..system_preset import install_system_preset
from ..user_config import UserConfigManager, resolve_active_username
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
            # 构造保证名册至少有一个用户；「当前用户」按与界面相同的规则取
            # （actives 没记就落在名册第一行），并在这里固化一次，之后无 UI 的
            # 读者（任务启动、离线同步）拿到的就是同一个用户。
            manager = UserConfigManager()
            active = resolve_active_username() or manager.list_users()[0]
            store = get_interface_store()
            if store.get_active("user", "") != active:
                store.set_active("user", active)
            if not store.get_active("layout", ""):
                store.set_active("layout", "android")
            store.set_active("env", "android")
            from ..profile.repository import get_profile_db
            get_profile_db()  # 首次建表；不写任何虚构业务值。
            return json.dumps({"ok": True, "updated": updated}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"ok": False, "message": f"预置配置初始化失败：{exc}"}, ensure_ascii=False)
