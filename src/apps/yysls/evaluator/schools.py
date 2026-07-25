"""流派注册与判定器工厂

流派清单来自 docs/10-game/04-tuning-mechanics.md「调律喜好配置」。
目前 会意流派-通用、会心流派-大外流/小外流、
治疗流派（纯奶/火拳奶子玩法合并）均已实现。
"""

from .base import Rating, SchoolJudge
from .heal import HealJudge
from .huiyi import HuiyiGeneralJudge
from .huixin import (
    SUB_SCHOOL_PLAYSTYLES, SUB_SCHOOLS, HuixinBigJudge, HuixinSmallJudge,
)

# 会心二级配置 SUB_SCHOOLS / SUB_SCHOOL_PLAYSTYLES 已下沉到 huixin，
# 此处再导出保持公共 API 不变。
__all__ = [
    "SUB_SCHOOLS", "SUB_SCHOOL_PLAYSTYLES", "SCHOOL_CLASSES", "SCHOOLS",
    "is_school_implemented", "get_school_judge", "judge_tuning_worthiness",
]


# ─── 流派注册表 ────────────────────────────────────────────

# key → 判定器类（UI 据类属性 has_keep_pvp/needs_sub_school/
# sub_school_options 生成配置控件）
SCHOOL_CLASSES: dict[str, type[SchoolJudge]] = {
    cls.school_key: cls
    for cls in (
        HuiyiGeneralJudge,
        HuixinSmallJudge,
        HuixinBigJudge,
        HealJudge,
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


def judge_tuning_worthiness(
    equip, configs: dict[str, dict] | None = None,
    schools: list[str] | None = None,
) -> tuple[bool, list[str]]:
    """遍历全部流派做调律潜力判定（or 语义）

    任一流派判定仍可达 顶级/优秀 → 值得调律。未实现的流派与
    不覆盖该部位的流派（not_applicable）不参与判定（不是否决票）；
    无任何流派能给出有效结论时判为不值得（直接结束）。

    Args:
        equip: EquipmentData
        configs: 流派 key → 配置 dict（缺省用默认配置）
        schools: 仅参与判定的流派 key 列表（None → 全部流派）

    Returns:
        (是否值得调律, 各流派判定明细文本)
    """
    worth = False
    conclusive = False  # 是否有流派给出了有效结论
    logs: list[str] = []
    for key, cls in SCHOOL_CLASSES.items():
        if schools is not None and key not in schools:
            continue
        try:
            res = cls((configs or {}).get(key)).check_tuning_worthiness(equip)
        except NotImplementedError:
            continue
        if res.not_applicable:
            logs.append(f"{cls.school_name}: 不适用（{'；'.join(res.reasons)}）")
            continue
        conclusive = True
        tag = "跳过" if res.skipped else res.rating.value
        logs.append(f"{cls.school_name}: {tag}（{'；'.join(res.reasons)}）")
        if not res.skipped and res.rating in (Rating.TOP, Rating.EXCELLENT):
            worth = True
    if not conclusive:
        worth = False
        logs.append("无已实现流派可判定该装备，结束调律")
    return worth, logs
