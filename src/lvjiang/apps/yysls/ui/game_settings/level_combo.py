"""等级下拉选择组件

统一从等级配置（get_game_config().get_level_configs()）获取可选等级列表，
避免各处重复构建下拉数据源。

使用场景：
- 独立模式：作为普通 QComboBox 使用（赛季配置、扫描处理门槛等）
- 表格单元格模式：作为 QTableWidget 的 cellWidget（基础属性、词组配置等）

数据源唯一入口：get_game_config().get_level_configs()
"""
from __future__ import annotations

from PyQt6.QtWidgets import QComboBox, QWidget

from lvjiang.apps.yysls.config import get_game_config

from .....i18n import tr


class LevelCombo(QComboBox):
    """装备等级下拉选择框

    自动从等级配置填充可选等级（降序排列，最高等级在前）。

    参数：
        allow_empty: 是否允许空选项（默认 False）
            - True: 首项为空白占位，get_level() 返回 None
            - False: 无空白项，get_level() 始终返回 int
        parent: 父组件
    """

    def __init__(
        self,
        allow_empty: bool = False,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._allow_empty = allow_empty
        self._refresh_items()

    def _refresh_items(self):
        """填充等级列表（降序）"""
        self.clear()
        if self._allow_empty:
            self.addItem("", None)
        configs = get_game_config().get_level_configs()
        levels = sorted([c.level for c in configs], reverse=True)
        if not levels:
            # 无等级配置时显示占位提示
            self.addItem(tr("（无等级配置）"), None)
            self.setEnabled(False)
        else:
            self.setEnabled(True)
            for level in levels:
                self.addItem(str(level), level)

    def refresh(self):
        """外部调用：等级配置变更后刷新列表"""
        current = self.get_level()
        self._refresh_items()
        # 尽量恢复之前的选中值
        if current is not None:
            idx = self.findData(int(current))
            if idx >= 0:
                self.setCurrentIndex(idx)
            elif self.count() > 0:
                # 原等级已不存在，回退到首项
                self.setCurrentIndex(0)

    def get_level(self) -> int | None:
        """获取当前选中的等级值

        allow_empty=True 且未选择时返回 None
        allow_empty=False 且无配置时返回 None（配置为空）
        """
        idx = self.currentIndex()
        if idx < 0:
            return None  # combo 无任何项
        data = self.currentData()
        if data is None:
            return None  # 选中了空占位项或无配置占位项
        return int(data)

    def set_level(self, value: int | None):
        """设置选中的等级值

        value 为 None 时：
            - allow_empty=True: 选择空白项
            - allow_empty=False: 不做任何操作
        value 不在候选列表中时：动态添加到候选列表
        """
        if value is None:
            if self._allow_empty:
                self.setCurrentIndex(0)
            return
        int_value = int(value)
        idx = self.findData(int_value)
        if idx >= 0:
            self.setCurrentIndex(idx)
        else:
            # 值不在候选列表，动态添加（回退保护）
            self.addItem(str(int_value), int_value)
            self.setCurrentIndex(self.findData(int_value))
