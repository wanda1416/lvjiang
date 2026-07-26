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


def part_label(equip: EquipmentData) -> str:
    """部位/武器描述文案（部位为武器时给出具体武器类型）"""
    if equip.part == "武器":
        return f"武器 {equip.weapon}"
    return f"部位 {equip.type}"


# ─── 判定结果 ──────────────────────────────────────────────

@dataclass
class JudgeResult:
    """单件装备判定结果

    skipped=True 表示品阶/首词条不符，无调律价值（直接跳过，不参与评级）。
    not_applicable=True 表示该流派不覆盖此部位，无法给出结论
    （不是否决票，多流派 or 判定时应忽略该结果）。
    is_pvp=True 表示装备因 PVP 词条（单体奇术增伤/对玩家增效）被保留。
    """
    equipment: EquipmentData
    rating: Rating = Rating.JUNK
    skipped: bool = False
    not_applicable: bool = False
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
        if self.not_applicable:
            d["not_applicable"] = True
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

    元数据（GenericSchoolJudge 构造时由规则填充为实例属性，
    类属性仅作默认值）:
    - school_key: 流派标识（配置持久化用）
    - school_name: 流派显示名
    - implemented: 判定逻辑是否已实现（未实现的流派 judge 抛 NotImplementedError）
    - has_keep_pvp: 该流派是否有「保留 PVP 装备」可选配置
    - needs_sub_school: 该流派是否需要子选项必选配置（至少勾选一项）
    - sub_school_options: 子选项 key → 显示名（UI 据此生成复选框）
    - sub_school_playstyles: 子选项 key → 玩法 key → 显示名（无玩法不列）
    - sub_school_label: 子选项分组的 UI 标签文本
    """

    school_key: str = ""
    school_name: str = ""
    implemented: bool = False
    has_keep_pvp: bool = False
    needs_sub_school: bool = False
    sub_school_options: dict[str, str] = {}
    sub_school_playstyles: dict[str, dict[str, str]] = {}
    sub_school_label: str = "指定流派（必选）："

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

    def check_tuning_worthiness(self, equip: EquipmentData) -> JudgeResult:
        """调律潜力判定：装备词条未满时判定是否值得（继续）调律

        把剩余空词条槽视作可变成任意词条的万能牌，返回该装备能达到的
        评级上限（rating）。rating ∈ {TOP, EXCELLENT} 且未 skipped
        视为值得调律；未实现的流派抛 NotImplementedError（调用方跳过）。
        """
        raise NotImplementedError(f"{self.school_name} 调律潜力判定暂未实现")
