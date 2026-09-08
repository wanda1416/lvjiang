"""游戏配置数据类

等级配置、赛季配置、品阶推断规则等数据结构定义。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class TuningStoneRule:
    """单品阶律准石规则。

    数值统一以「0.1 枚大律准石」为内部单位存储。
    tune_cost 的 key 是即将写入的目标词条序号；
    reset_refund/recycle_refund 的 key 是操作前的当前词条数。
    返还值是该装备当前进度的累计返还，不是单轮增量。
    """

    tune_cost: dict[int, int] = field(default_factory=dict)
    reset_refund: dict[int, int] = field(default_factory=dict)
    recycle_refund: dict[int, int] = field(default_factory=dict)

# ─── 等级配置数据结构 ──────────────────────────────────────

@dataclass
class LevelConfig:
    """单等级配置条目

    level: 装备等级（必填）
    allow_reset: 该等级是否支持重置调律（可选，None 表示未设置）
    allow_chengyin: 该等级是否支持承音
    allow_retransfer: 该等级是否支持无限再次转律
    allow_retransfer_after_chengyin: 该原始等级承音后是否仍支持再次转律
    min_material_count: 该等级要求的最低材料数量（可选，None 表示未设置）
    judge_resistance: 判定抗性百分比（可选，>= 0）
    buff_resistance: 增益抗性百分比（可选，>= 0）
    reset_no_refund: 该等级重置是否固定不返还律准石
    tuning_stones: gold/purple 的调律消耗与累计返还规则
    """
    level: int = 0
    allow_reset: bool | None = None
    allow_chengyin: bool = False
    allow_retransfer: bool = False
    allow_retransfer_after_chengyin: bool = False
    min_material_count: int | None = None
    judge_resistance: int | None = None
    buff_resistance: int | None = None
    reset_no_refund: bool = True
    tuning_stones: dict[str, TuningStoneRule] = field(default_factory=dict)


# ─── 赛季配置数据结构 ──────────────────────────────────────

@dataclass
class SeasonConfig:
    """单赛季配置条目

    season_number: 赛季编号（1, 2, 3...，用于排序）
    name: 赛季名称（如"黄钟长鸣"、"夹钟并作"）
    start_date: 赛季开始日期
    end_date: 赛季结束日期
    first_half_end_date: 上半赛季结束日期（每赛季 84 天，前 42 天为上半赛季）
    equip_level: 当前赛季装备等级（如 90, 96, 100）
    """
    season_number: int = 0
    name: str = ""
    start_date: date | None = None
    end_date: date | None = None
    first_half_end_date: date | None = None
    equip_level: int | None = None


# ─── 品阶推断数据结构 ──────────────────────────────────────

@dataclass
class AttrRange:
    """单个品阶的属性值规则

    区间属性（武器）：[min_val, max_val] 表示该品阶装备提供
        +min_val 最小外功攻击、+max_val 最大外功攻击（并非取值区间）。
    点值属性（首饰/防具）：min_val == max_val。
    """
    quality: str           # gold / purple / blue
    min_val: int | None = None
    max_val: int | None = None

    def matches(self, value: int | list | tuple) -> bool:
        """判定解析出的基础属性值是否精确命中本品阶。

        区间属性：解析出的区间 [c, d] 必须 c==min_val 且 d==max_val，
            而非"落在区间内"（因相邻品阶区间会重叠）。
        点值属性：解析出的标量须精确等于该值（min_val==max_val）。
        """
        if isinstance(value, (list, tuple)):
            return (len(value) >= 2
                    and value[0] == self.min_val and value[1] == self.max_val)
        # 标量：仅点值属性（min==max）可命中
        return self.min_val == self.max_val == value


@dataclass
class LevelRule:
    """某个分类在某个等级的品阶规则"""
    ranges: list[AttrRange] = field(default_factory=list)

    def infer_quality(self, value: int | list | tuple) -> str | None:
        for r in self.ranges:
            if r.matches(value):
                return r.quality
        return None
