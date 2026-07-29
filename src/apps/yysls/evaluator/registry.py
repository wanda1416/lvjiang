"""调律规则注册与判定器工厂（规则驱动）

调律规则清单来自 config/system/yysls/tuning_rules/ 下的规则 YAML
（TuningRuleManager 加载），判定统一由 GenericTuningJudge 完成。
规则变更（含 UI「装备调律配置」编辑保存）后 reload 即生效。
"""

from .base import Rating, TuningJudge
from .judge import GenericTuningJudge
from .tuning_rules import TuningRule, get_tuning_rule_manager

__all__ = [
    "get_tuning_rules", "get_rule_names",
    "is_rule_implemented", "get_tuning_judge",
    "judge_equipment_potential", "judge_tuning_worthiness",
    "summarize_potential",
]


# ─── 调律规则注册表（每次查询管理器，保证 reload 后不过期） ───

def get_tuning_rules() -> dict[str, TuningRule]:
    """key → TuningRule（按规则 order 排序；UI 据其元数据属性
    playstyle_options 生成玩法勾选控件）"""
    return get_tuning_rule_manager().get_rules()


def get_rule_names() -> dict[str, str]:
    """key → 显示名（供 UI 使用，保持规则 order 顺序）"""
    return {key: rule.name for key, rule in get_tuning_rules().items()}


def is_rule_implemented(rule_key: str) -> bool:
    """规则判定逻辑是否已实现（规则加载成功即已实现）"""
    return get_tuning_rule_manager().get_rule(rule_key) is not None


def get_tuning_judge(rule_key: str, config: dict | None = None) -> TuningJudge:
    """创建指定调律规则的判定器实例

    Args:
        rule_key: 规则标识（规则 YAML 的 key）
        config: 该规则的配置 dict，形状 {"playstyles": [...],
            "switches": {开关 key: bool}}（switches 为全局配置，由调用方注入）

    Raises:
        ValueError: 规则标识未注册
    """
    rule = get_tuning_rule_manager().get_rule(rule_key)
    if rule is None:
        raise ValueError(f"未知调律规则: {rule_key}")
    return GenericTuningJudge(rule, config)


def judge_equipment_potential(
    equip, configs: dict[str, dict] | None = None,
    rule_keys: list[str] | None = None,
) -> dict[str, dict]:
    """遍历全部调律规则做含转律模拟的评级上限判定（结构化结果）

    与 judge_tuning_worthiness 同一判定内核（check_tuning_worthiness：
    空槽万能牌 + 模拟转律；词条已满时即纯转律模拟），返回各规则
    结构化评级，供单次调律终局分析与自动调律直接消费。

    Args:
        equip: EquipmentData
        configs: 规则 key → 配置 dict（缺省用默认配置）
        rule_keys: 仅参与判定的规则 key 列表（None → 全部规则）

    Returns:
        规则 key → {"name", "rating", "skipped", "not_applicable",
        "reasons"}（未实现判定的规则不在结果中；skipped/not_applicable
        为 True 时 rating 无实际意义）
    """
    results: dict[str, dict] = {}
    for key, rule in get_tuning_rules().items():
        if rule_keys is not None and key not in rule_keys:
            continue
        judge = GenericTuningJudge(rule, (configs or {}).get(key))
        try:
            res = judge.check_tuning_worthiness(equip)
        except NotImplementedError:
            continue
        results[key] = {
            "name": rule.name,
            "rating": res.rating.value,
            "skipped": res.skipped,
            "not_applicable": res.not_applicable,
            "reasons": res.reasons,
        }
    return results


def summarize_potential(results: dict[str, dict]) -> tuple[bool, list[str]]:
    """把 judge_equipment_potential 的结构化结果归纳为 (是否值得, 明细文本)

    任一规则评级为 顶级/优秀 → 值得调律。不适用规则不参与判定
    （不是否决票）；无任何规则给出有效结论时判为不值得。
    独立成函数供调用方对同一份结构化结果同时做文本归纳与筛选
    （如说明文档只取命中的规则），避免二次判定。
    """
    worth = False
    conclusive = False  # 是否有规则给出了有效结论
    logs: list[str] = []
    for r in results.values():
        detail = '；'.join(r["reasons"])
        if r["not_applicable"]:
            logs.append(f"{r['name']}: 不适用（{detail}）")
            continue
        conclusive = True
        tag = "跳过" if r["skipped"] else r["rating"]
        logs.append(f"{r['name']}: {tag}（{detail}）")
        if not r["skipped"] and r["rating"] in (Rating.TOP.value,
                                               Rating.EXCELLENT.value):
            worth = True
    if not conclusive:
        worth = False
        logs.append("无已实现规则可判定该装备，结束调律")
    return worth, logs


def judge_tuning_worthiness(
    equip, configs: dict[str, dict] | None = None,
    rule_keys: list[str] | None = None,
) -> tuple[bool, list[str]]:
    """遍历全部调律规则做调律潜力判定（or 语义）

    任一规则判定仍可达 顶级/优秀 → 值得调律。未实现的规则与
    不覆盖该部位的规则（not_applicable）不参与判定（不是否决票）；
    无任何规则能给出有效结论时判为不值得（直接结束）。

    Args:
        equip: EquipmentData
        configs: 规则 key → 配置 dict（缺省用默认配置）
        rule_keys: 仅参与判定的规则 key 列表（None → 全部规则）

    Returns:
        (是否值得调律, 各规则判定明细文本)
    """
    return summarize_potential(
        judge_equipment_potential(equip, configs, rule_keys))
