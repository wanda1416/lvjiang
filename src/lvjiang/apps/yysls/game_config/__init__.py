"""游戏配置管理器

统一管理 attributes.yaml 中的游戏基础配置数据：
1. 基础属性品阶推断（原 EquipAttrConfig）
2. 词条上限查询（含承音值）
3. 真实词条名 → 配置类别名 映射
4. 词库类型查询（普通词条 / 定音词条，YAML 中用 _pool: dingyin 声明）
5. 官方流派与武器注册表（schools 节，get_schools）

数据来源：config/system/yysls/attributes.yaml
映射关系通过 YAML 中每个类别的 _aliases 字段声明，支持 UI 动态管理。
_aliases 支持两种形态：list（不分组）或 dict（分组：组名 → 词条名列表，
如 指定技能增效 按十大流派分组）。
"""

from .constants import (
    AFFIX_CATEGORY_NAMES,
    BASE_ATTR_PARTS,
    EQUIP_PART_NAMES,
    POOL_DINGYIN,
    POOL_NORMAL,
    WUXUE_CATEGORY,
)
from .manager import GameConfigManager, get_game_config
from .models import AttrRange, LevelConfig, LevelRule

__all__ = [
    "AFFIX_CATEGORY_NAMES",
    "AttrRange",
    "BASE_ATTR_PARTS",
    "EQUIP_PART_NAMES",
    "GameConfigManager",
    "LevelConfig",
    "LevelRule",
    "POOL_DINGYIN",
    "POOL_NORMAL",
    "WUXUE_CATEGORY",
    "get_game_config",
]
