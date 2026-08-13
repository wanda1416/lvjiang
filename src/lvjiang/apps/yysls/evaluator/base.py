"""装备判定公共基类

定义四档评级、判定结果数据结构和调律规则判定器接口。
评级采用穷举匹配制（参考 docs/10-game/11-调律说明文档/01-会意流派调律说明.md）。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

from lvjiang.apps.yysls.equip_parser import EquipmentData

from ....i18n import tr

# ─── 评级枚举 ──────────────────────────────────────────────

class Rating(Enum):
    """装备评级（穷举匹配制四档）"""
    TOP = tr("顶级")
    EXCELLENT = tr("优秀")
    NORMAL = tr("一般")
    JUNK = tr("垃圾")


def part_label(equip: EquipmentData) -> str:
    """部位/武器描述文案（部位为武器时给出具体武器类型）"""
    if equip.part == tr("武器"):
        return f"武器 {equip.weapon}"
    return f"部位 {equip.type}"


# ─── 判定结果 ──────────────────────────────────────────────

@dataclass
class JudgeResult:
    """单件装备判定结果

    skipped=True 表示品阶/首词条不符，无调律价值（直接跳过，不参与评级）。
    not_applicable=True 表示该规则不覆盖此部位，无法给出结论
    （不是否决票，多规则 or 判定时应忽略该结果）。
    """
    equipment: EquipmentData
    rating: Rating = Rating.JUNK
    skipped: bool = False
    not_applicable: bool = False
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
        if self.reasons:
            d["reasons"] = self.reasons
        return d


# ─── 调律规则判定器基类 ────────────────────────────────────────

class TuningJudge(ABC):
    """调律规则判定器公共基类

    子类需实现:
    - judge: 对完整装备进行穷举匹配定级

    元数据（GenericTuningJudge 构造时由规则填充为实例属性，
    类属性仅作默认值）:
    - rule_key: 规则标识（配置持久化用）
    - rule_name: 规则显示名
    - implemented: 判定逻辑是否已实现（未实现的规则 judge 抛 NotImplementedError）
    - playstyle_options: 玩法名字 → 摘要（UI 据此生成复选框）

    config 形状：{"playstyles": [...], "switches": {开关 key: bool}}，
    其中 switches 为全局开关状态（由调用方注入，未配置的开关视作
    False）。
    """

    rule_key: str = ""
    rule_name: str = ""
    implemented: bool = False
    playstyle_options: dict[str, str] = {}

    def __init__(self, config: dict | None = None):
        self.config: dict = config or {}
        self.switches: dict[str, bool] = {
            str(k): bool(v)
            for k, v in (self.config.get("switches") or {}).items()}

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
        视为值得调律；未实现的规则抛 NotImplementedError（调用方跳过）。
        """
        raise NotImplementedError(f"{self.rule_name} 调律潜力判定暂未实现")
