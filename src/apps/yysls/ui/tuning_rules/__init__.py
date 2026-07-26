"""装备调律规则编辑 UI 包

「燕云 → 装备调律规则」菜单入口，对 config/system/yysls/
tuning_rules/ 下的规则 YAML 做全量结构化编辑（自动保存 + reload）。
"""

from .rules_dialog import TuningRulesDialog

__all__ = ["TuningRulesDialog"]
