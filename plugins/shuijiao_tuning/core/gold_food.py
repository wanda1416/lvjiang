"""用户级、按装备等阶的金狗粮策略。2026-10-31 新增。"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

GOLD_FOOD = "金狗粮"


@dataclass(frozen=True)
class GoldFoodPolicy:
    enabled: bool = False
    min_initial_pct: float = 85.0
    low_stock: int = 10
    reserve_enabled: bool = False
    reserve_pct: float = 95.0
    reserve_parts: tuple[str, ...] = ()
    color_enabled: bool = False
    color_pct: float = 98.0
    color_low_stock: int = 0
    keep_enabled: bool = False
    keep_pct: float = 95.0
    refund_wait: float = 2.0

    @classmethod
    def from_config(cls, raw: dict | None) -> GoldFoodPolicy:
        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raise ValueError("金狗粮配置必须是对象")
        enabled = raw.get("enabled", False)
        pct = raw.get("min_initial_pct", 85.0)
        count = raw.get("low_stock", 10)
        if not isinstance(enabled, bool):
            raise ValueError("金狗粮策略开关必须是布尔值")
        if (type(pct) not in (int, float) or not math.isfinite(pct)
                or not 0 <= pct <= 100):
            raise ValueError("首词条用金门槛须为 0–100%")
        if type(count) is not int or count < 0:
            raise ValueError("金狗粮停用线须为非负整数")
        reserve_enabled = raw.get("reserve_enabled", False)
        reserve_pct = raw.get("reserve_pct", 95.0)
        parts = raw.get("reserve_parts", [])
        from lvjiang.apps.yysls.config.equipment_slots import EQUIPMENT_SLOTS
        if not isinstance(reserve_enabled, bool) or type(reserve_pct) not in (int, float) or not math.isfinite(reserve_pct) or not 0 <= reserve_pct <= 100:
            raise ValueError("武库预留门槛须为0–100%")
        if not isinstance(parts, (list, tuple)) or any(p not in EQUIPMENT_SLOTS for p in parts):
            raise ValueError("武库预留部位无效")
        extra = {"color_enabled": raw.get("color_enabled", False), "color_pct": raw.get("color_pct", 98.0),
                 "color_low_stock": raw.get("color_low_stock", 0), "keep_enabled": raw.get("keep_enabled", False),
                 "keep_pct": raw.get("keep_pct", 95.0), "refund_wait": raw.get("refund_wait", 2.0)}
        if any(not isinstance(extra[k], bool) for k in ("color_enabled", "keep_enabled")):
            raise ValueError("彩狗粮及回收保护开关须为布尔值")
        if type(extra["color_low_stock"]) is not int or extra["color_low_stock"] < 0:
            raise ValueError("彩狗粮停用线须为非负整数")
        if any(type(extra[k]) not in (int, float) or not math.isfinite(extra[k]) or not 0 <= extra[k] <= 100 for k in ("color_pct", "keep_pct")):
            raise ValueError("首条比例须为0–100%")
        if type(extra["refund_wait"]) not in (int, float) or not math.isfinite(extra["refund_wait"]) or not 0.5 <= extra["refund_wait"] <= 10:
            raise ValueError("返还动画最短等待须为0.5–10秒")
        return cls(enabled, float(pct), count, reserve_enabled, float(reserve_pct), tuple(parts), **extra)

    def to_config(self) -> dict:
        raw = asdict(self)
        raw["reserve_parts"] = list(self.reserve_parts)
        return raw

    def wants_gold(self, level: int | float | None, pct: float | None,
                   *, first_round: bool, viable: bool) -> tuple[bool, str]:
        if level == 105:
            return True, "105 阶忽略数值，每轮使用一份金狗粮"
        if level not in (110, 115):
            return False, "等级金狗粮策略仅处理 105/110/115 阶"
        if pct is None or not math.isfinite(pct):
            return False, "首词条上限比例未识别，不自动投入金狗粮"
        if pct < self.min_initial_pct:
            return False, f"首词条 {pct:g}% < {self.min_initial_pct:g}%，不投入金狗粮"
        if first_round:
            return True, f"首词条 {pct:g}% 达标，先投入一份金狗粮试调"
        if viable:
            return True, "现有词条仍可达标（含一次合法转律），继续用金狗粮"
        return False, "当前流派及跨流派顶级潜力不足，不继续投入金狗粮"

    def reserve_matches(self, equip) -> bool:
        from lvjiang.apps.yysls.config.equipment_slots import SLOT_BY_KEY
        pct = equip.affixes[0].cap_pct if equip.affixes else None
        return bool(self.reserve_enabled and equip.level == 115 and pct is not None
                    and math.isfinite(pct) and pct > self.reserve_pct
                    and any(SLOT_BY_KEY[k].part == equip.part for k in self.reserve_parts))
