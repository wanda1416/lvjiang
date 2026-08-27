"""向 core.config 声明燕云配置文件的合并策略与版本化目录。

core.config 是通用引擎模块，不认识任何游戏领域词汇——`base_rules` 是登记表
还是枚举设定、`weapon_types` 该按哪个字段判断"是不是同一个武器类型"、
`yysls/tuning_rules/` 参不参与在线下发，都是纯燕云业务知识，不该写进
`core/config/` 的常量表。

本模块经 `AppHooks.config_policy_modules`「import 即注册」触发（同
`builtin_modules`/`telemetry_modules` 的约定，见 `apps/yysls/__init__.py`），
在插件加载时把这些声明登记到 resolver 里；import 之外不做任何事。
"""
from __future__ import annotations

from ....core.config.resolver import (
    register_protected_list_paths,
    register_registry_list_paths,
)
from ....core.config.versioning import register_versioned_dir

# tune_config.yaml 的 base_rules：可增长的基础规则组登记表，不是枚举设定
# ——local 若存下完整列表，出厂新增的规则组就永远进不到合并视图。
register_registry_list_paths("yysls/tune_config.yaml", ("base_rules",))

# game_config.yaml 的三张受保护列表：出厂条目不允许被移除，但可以改值、
# 可以新增；身份字段用于在 local 存了不完整列表时补回缺失的出厂条目。
register_protected_list_paths("yysls/game_config.yaml", {
    "weapon_types": "name",
    "level_configs": "level",
    "season_configs": "season_number",
})

# 调律规则参与 remote 在线下发（带 content_version、可被远端更新）。
#
# allow_remote_new=True 是这三类里唯一的例外：规则管理器对「未在
# tune_config.tuning_rules 里声明的规则」是追加到末尾而不是报错（见
# tuning_rules/manager.py 的 _reload），所以远端下发一条全新规则能直接
# 生效，不用等发版——这正是在线下发最有价值的场景。scenes/layouts 则
# 相反：新增文件要在 scenes.yaml 注册表里登记才有意义，而注册表走发版。
register_versioned_dir("yysls/tuning_rules", "*.yaml", depth=1,
                       allow_remote_new=True)
