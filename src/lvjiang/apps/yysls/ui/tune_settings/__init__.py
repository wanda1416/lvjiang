"""装备调律配置编辑 UI 包

「燕云 → 装备调律规则」菜单入口，对 config/system/yysls/
tuning_rules/ 下的规则 YAML 做全量结构化编辑（对话框暂存，统一保存或撤销）。
"""

from .rules_dialog import TuningRulesDialog

__all__ = ["TuningRulesDialog"]
