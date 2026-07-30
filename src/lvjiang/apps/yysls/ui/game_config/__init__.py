"""游戏配置编辑 UI 包

「燕云 → 游戏配置」菜单入口，管理 attributes.yaml：
词组配置、装备配置（基础属性 + 武器类型）与流派配置。
"""

from .config_dialog import GameConfigDialog

__all__ = ["GameConfigDialog"]
