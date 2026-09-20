"""毕业率计算假设：一份数据结构、一个投影入口。

备战方案面板、毕业率分析对话框（最优组合 / 转律建议 / 培养建议）、智能调律
都用同一个 ``Assumptions`` 描述“把装备看成什么状态来算”，避免各入口各自
维护一套开关与投影实现。投影本身仍由 ``apply_hypothetical_caps`` 完成。
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from .....i18n import tr
from ..combat.combat_attrs import apply_hypothetical_caps


@dataclass(frozen=True)
class Assumptions:
    """计算假设。全部关闭时投影即原样。

    - ``full_level``：>0 时低于该等级的装备升到该等级（并视为承音）。
    - ``full_chengyin`` / ``full_dingyin``：承音装备普通词条 / 定音顶到上限。
    - ``simulate_transmute``：按装备上已保存的转律目标计算。
    - ``playstyle``：满定音时按该玩法要求的定音替换。
    """

    full_level: int = 0
    full_chengyin: bool = False
    full_dingyin: bool = False
    simulate_transmute: bool = False
    playstyle: str = ""

    def project(self, equipped: dict, game_config=None) -> dict:
        """返回按本假设变换后的装备副本；无假设时返回原 dict。"""
        return apply_hypothetical_caps(
            equipped,
            full_chengyin=self.full_chengyin,
            full_dingyin=self.full_dingyin,
            full_level=self.full_level,
            playstyle=self.playstyle,
            simulate_transmute=self.simulate_transmute,
        )

    def labels(self) -> tuple[str, ...]:
        """已启用假设的展示名，供结果标注“基于：…”。"""
        result: list[str] = []
        if self.full_level:
            result.append(tr("满等级"))
        if self.full_chengyin:
            result.append(tr("满承音"))
        if self.full_dingyin:
            result.append(tr("满定音"))
        if self.simulate_transmute:
            result.append(tr("模拟转律"))
        return tuple(result)

    def with_playstyle(self, playstyle: str) -> Assumptions:
        return replace(self, playstyle=playstyle)
