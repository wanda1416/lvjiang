"""流派注册与判定器工厂（规则驱动）

流派清单来自 config/system/yysls/tuning_rules/ 下的规则 YAML
（TuningRuleManager 加载），判定统一由 GenericSchoolJudge 完成。
规则变更（含 UI「装备调律规则」编辑保存）后 reload 即生效。
"""

from .base import Rating, SchoolJudge
from .generic import GenericSchoolJudge
from .rules import SchoolRule, get_tuning_rule_manager

__all__ = [
    "get_school_rules", "get_schools",
    "is_school_implemented", "get_school_judge", "judge_tuning_worthiness",
]


# ─── 流派注册表（每次查询管理器，保证 reload 后不过期） ───

def get_school_rules() -> dict[str, SchoolRule]:
    """key → SchoolRule（按规则 order 排序；UI 据其元数据属性
    has_keep_pvp/needs_sub_school/sub_school_options 生成配置控件）"""
    return get_tuning_rule_manager().get_rules()


def get_schools() -> dict[str, str]:
    """key → 显示名（供 UI 使用，保持规则 order 顺序）"""
    return {key: rule.name for key, rule in get_school_rules().items()}


def is_school_implemented(school: str) -> bool:
    """流派判定逻辑是否已实现（规则加载成功即已实现）"""
    return get_tuning_rule_manager().get_rule(school) is not None


def get_school_judge(school: str, config: dict | None = None) -> SchoolJudge:
    """创建指定流派的判定器实例

    Args:
        school: 流派标识（规则 YAML 的 key）
        config: 该流派的配置 dict，如 {"keep_pvp": True,
            "sub_schools": [...], "playstyles": {...}}

    Raises:
        ValueError: 流派标识未注册
    """
    rule = get_tuning_rule_manager().get_rule(school)
    if rule is None:
        raise ValueError(f"未知流派: {school}")
    return GenericSchoolJudge(rule, config)


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
    for key, rule in get_school_rules().items():
        if schools is not None and key not in schools:
            continue
        judge = GenericSchoolJudge(rule, (configs or {}).get(key))
        try:
            res = judge.check_tuning_worthiness(equip)
        except NotImplementedError:
            continue
        if res.not_applicable:
            logs.append(f"{rule.name}: 不适用（{'；'.join(res.reasons)}）")
            continue
        conclusive = True
        tag = "跳过" if res.skipped else res.rating.value
        logs.append(f"{rule.name}: {tag}（{'；'.join(res.reasons)}）")
        if not res.skipped and res.rating in (Rating.TOP, Rating.EXCELLENT):
            worth = True
    if not conclusive:
        worth = False
        logs.append("无已实现流派可判定该装备，结束调律")
    return worth, logs
