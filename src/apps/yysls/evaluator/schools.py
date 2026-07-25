"""流派注册与判定器工厂

流派清单来自 docs/10-game/04-tuning-mechanics.md「调律喜好配置」。
目前仅 会意流派-通用 已实现，其余流派为空实现占位。
"""

from .base import SchoolJudge
from .huiyi import HuiyiGeneralJudge


# ─── 会心流派二级配置（指定流派 + 玩法）──────────────

# 指定流派 key → 显示名
SUB_SCHOOLS: dict[str, str] = {
    "lieshi": "裂石",
    "pozhu": "破竹",
    "qiansi": "牵丝",
}

# 指定流派 key → 玩法 key → 显示名（破竹无玩法区分，不列条目）
SUB_SCHOOL_PLAYSTYLES: dict[str, dict[str, str]] = {
    "lieshi": {"chuntang": "纯唐", "shuangqie": "双切"},
    "qiansi": {"zoudi": "走地", "feitian": "飞天"},
}


# ─── 空实现占位流派 ────────────────────────────────────────

class HuixinSmallJudge(SchoolJudge):
    """会心流派-小外流（未实现）"""

    school_key = "huixin_small"
    school_name = "会心流派-小外流"
    implemented = False
    has_keep_pvp = True
    needs_sub_school = True

    def judge(self, equip):
        raise NotImplementedError("会心流派-小外流 判定暂未实现")


class HuixinBigJudge(SchoolJudge):
    """会心流派-大外流（未实现）"""

    school_key = "huixin_big"
    school_name = "会心流派-大外流"
    implemented = False
    has_keep_pvp = True
    needs_sub_school = True

    def judge(self, equip):
        raise NotImplementedError("会心流派-大外流 判定暂未实现")


class HealPureJudge(SchoolJudge):
    """治疗流派-纯奶（未实现）"""

    school_key = "heal_pure"
    school_name = "治疗流派-纯奶"
    implemented = False

    def judge(self, equip):
        raise NotImplementedError("治疗流派-纯奶 判定暂未实现")


class HealFireJudge(SchoolJudge):
    """治疗流派-火拳奶（输出）（未实现）"""

    school_key = "heal_fire"
    school_name = "治疗流派-火拳奶（输出）"
    implemented = False

    def judge(self, equip):
        raise NotImplementedError("治疗流派-火拳奶 判定暂未实现")


# ─── 流派注册表 ────────────────────────────────────────────

# key → 判定器类（UI 据类属性 has_keep_pvp/needs_sub_school 生成配置控件）
SCHOOL_CLASSES: dict[str, type[SchoolJudge]] = {
    cls.school_key: cls
    for cls in (
        HuiyiGeneralJudge,
        HuixinSmallJudge,
        HuixinBigJudge,
        HealPureJudge,
        HealFireJudge,
    )
}

# key → 显示名（供 UI 使用，保持定义顺序）
SCHOOLS: dict[str, str] = {
    key: cls.school_name for key, cls in SCHOOL_CLASSES.items()
}


def is_school_implemented(school: str) -> bool:
    """流派判定逻辑是否已实现"""
    cls = SCHOOL_CLASSES.get(school)
    return cls is not None and cls.implemented


def get_school_judge(school: str, config: dict | None = None) -> SchoolJudge:
    """创建指定流派的判定器实例

    Args:
        school: 流派标识（SCHOOLS 的 key）
        config: 该流派的配置 dict，如 {"keep_pvp": True,
            "sub_schools": [...], "playstyles": {...}}

    Raises:
        ValueError: 流派标识未注册
    """
    cls = SCHOOL_CLASSES.get(school)
    if cls is None:
        raise ValueError(f"未知流派: {school}")
    return cls(config)
