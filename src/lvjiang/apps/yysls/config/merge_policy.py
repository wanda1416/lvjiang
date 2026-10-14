"""向 core.config 声明燕云配置文件的合并策略与版本化目录。

core.config 是通用引擎模块，不认识游戏领域词汇。`weapon_types` 的条目身份、
`yysls/tuning_rules/` 的加载与校验类型，都是纯燕云业务知识，不该写进
`core/config/` 的常量表。

本模块经 `AppHooks.config_policy_modules`「import 即注册」触发（同
`builtin_modules`/`telemetry_modules` 的约定，见 `apps/yysls/__init__.py`），
在插件加载时把这些声明登记到 resolver 里；import 之外不做任何事。
"""
from __future__ import annotations

from ....core.config.resolver import (
    register_protected_list_paths,
)
from ....core.config.versioning import (
    register_versioned_dir,
    register_versioned_file,
)
from .builds import GEAR_SETS_DIR
from .game_config_files import GAME_CONFIG_FILES

# 游戏配置的三张受保护列表：系统条目不允许被移除，但可以改值、
# 可以新增；身份字段用于在 local 存了不完整列表时补回缺失的系统条目。
register_protected_list_paths("yysls/game_config/equipment.yaml", {
    "weapon_types": "name",
})
register_protected_list_paths("yysls/game_config/seasons.yaml", {
    "level_configs": "level",
    "season_configs": "season_number",
})

# 两份聚合业务配置也允许在线更新。读取时已安装 system 作为系统基底，用户已有的
# local diff 继续叠加在它上面，因此不会覆盖用户自己的设置。
for _game_config_file in GAME_CONFIG_FILES:
    register_versioned_file(_game_config_file)
register_versioned_file("yysls/tune_config.yaml")
register_versioned_dir(GEAR_SETS_DIR, "*.yaml", depth=1)

# 已知实体登记仅服务加载校验和编辑器，不限制分发。
register_versioned_dir("yysls/tuning_rules", "*.yaml", depth=1)
# 基础规则组同样目录驱动（存在性由目录决定、order 排序），远程可以直接
# 下发一套新组。
register_versioned_dir("yysls/base_groups", "*.yaml", depth=1)

# 毕业率模型的身份写在文件自己里（schema_version + 流派/方案/等级/版本），
# 发现层只认当前 schema 且四项齐全的实体，所以可随远程下发全新方案文件，
# 不需要另在 game_config 里登记一份方案名清单。
# 模型按等级分子目录（graduation/<等级>级/<流派>_<方案>_v<版本>.json），
# 所以深度是 2：等级膨胀时不至于把一个目录堆到几十个文件。
register_versioned_dir("yysls/graduation", "*.json", depth=2)
