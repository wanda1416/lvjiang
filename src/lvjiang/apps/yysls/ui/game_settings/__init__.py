"""游戏配置与属性配置的编辑 UI 包

- 「燕云 → 游戏配置」（F5）：词组配置、装备配置（基础属性 + 武器类型）
  与流派配置等，管理 game_config.yaml；
- 「燕云 → 属性配置」（F7）：装备之外的战斗属性从哪来（心法、武学天赋、
  套装、突破…），以及由它们推导基础属性，管理 attr_model/。

两者分开是因为填写节奏完全不同：游戏配置是一次配好基本不动的规则，
属性来源是几百条要长期补的数值。
"""

from .attr_config_dialog import AttrConfigDialog
from .config_dialog import GameConfigDialog

__all__ = ["AttrConfigDialog", "GameConfigDialog"]
