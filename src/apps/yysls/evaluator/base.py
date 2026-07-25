"""装备判定公共基类

定义四档评级、判定结果数据结构和流派判定器接口。
评级采用穷举匹配制（参考 docs/10-game/11-调律说明文档/01-会意流派调律说明.md）。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

from src.apps.yysls.equip_parser import EquipmentData


# ─── 评级枚举 ──────────────────────────────────────────────

class Rating(Enum):
    """装备评级（穷举匹配制四档）"""
    TOP = "顶级"
    EXCELLENT = "优秀"
    USABLE = "能用"
    JUNK = "垃圾"


# ─── 判定结果 ──────────────────────────────────────────────

@dataclass
class JudgeResult:
    """单件装备判定结果

    skipped=True 表示品阶/首词条不符，无调律价值（直接跳过，不参与评级）。
    is_pvp=True 表示装备因 PVP 词条（单体奇术增伤/对玩家增效）被保留。
    """
    equipment: EquipmentData
    rating: Rating = Rating.JUNK
    skipped: bool = False
    is_pvp: bool = False
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict = {
            "name": self.equipment.name,
            "type": self.equipment.type,
            "level": self.equipment.level,
            "is_chengyin": self.equipment.is_chengyin,
            "rating": self.rating.value,
            "skipped": self.skipped,
        }
        if self.is_pvp:
            d["is_pvp"] = True
        if self.reasons:
            d["reasons"] = self.reasons
        return d


# ─── 流派判定器基类 ────────────────────────────────────────

class SchoolJudge(ABC):
    """流派判定器公共基类

    子类需实现:
    - judge: 对完整装备进行穷举匹配定级

    类属性:
    - school_key: 流派标识（配置持久化用）
    - school_name: 流派显示名
    - implemented: 判定逻辑是否已实现（未实现的流派 judge 抛 NotImplementedError）
    - has_keep_pvp: 该流派是否有「保留 PVP 装备」可选配置
    - needs_sub_school: 该流派是否需要「指定流派 + 玩法」必选配置
    """

    school_key: str = ""
    school_name: str = ""
    implemented: bool = False
    has_keep_pvp: bool = False
    needs_sub_school: bool = False

    def __init__(self, config: dict | None = None):
        self.config: dict = config or {}
        self.keep_pvp: bool = bool(self.config.get("keep_pvp", False))

    @abstractmethod
    def judge(self, equip: EquipmentData) -> JudgeResult:
        """判定单件装备评级

        Args:
            equip: 已解析的标准装备数据

        Returns:
            JudgeResult
        """
        ...

    def check_tuning_worthiness(self, equip: EquipmentData) -> bool:
        """调律熔断判定（预留接口，暂未实现）

        规格（01 文档第八节）：假设将当前非首词条中最差的一条替换为
        转律词库中的最佳词条后重新定级，能达「能用」及以上则继续调律。
        """
        raise NotImplementedError("调律熔断判定暂未实现")
