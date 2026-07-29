"""燕云「装备状态」Tab —— 通过 AppHooks 注入通用 MainWindow 的插件页面。

包装 EquipStatusPanel：构建时刷新一次，并订阅面板刷新请求与宿主用户切换信号。
"""
from __future__ import annotations

from loguru import logger

from .equip_status_panel import EquipStatusPanel


class EquipStatusTab(EquipStatusPanel):
    """装备状态 Tab（host 为通用 MainWindow，提供 active_user_name / user_changed）"""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self.refresh_requested.connect(self.refresh_from_user)
        host.user_changed.connect(lambda _name: self.refresh_from_user())
        self.refresh_from_user()

    def refresh_from_user(self):
        """从当前用户的本地配置加载已装备数据并刷新面板"""
        import json
        from src.constants import LOCAL_CONFIG_DIR

        user_name = self._host.active_user_name()
        if not user_name:
            self.refresh({})
            return
        user_file = LOCAL_CONFIG_DIR / "users" / f"{user_name}.json"
        if not user_file.exists():
            self.refresh({})
            return
        try:
            data = json.loads(user_file.read_text(encoding="utf-8"))
            equipped = data.get("equipped", {})
            self.refresh(equipped)
        except Exception as e:
            logger.error(f"加载用户装备数据失败: {e}")
            self.refresh({})
