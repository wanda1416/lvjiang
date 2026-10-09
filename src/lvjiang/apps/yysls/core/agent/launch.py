"""The shared UI/Agent contract for preparing an automatic tuning run."""
from __future__ import annotations

from copy import deepcopy

from ...workflows.tuning_context import TuningRunContext
from ..evaluator import get_tuning_judge, get_tuning_rules, is_rule_implemented
from ..tuning_rules import get_tuning_group


def validate_run_fields(config: dict) -> None:
    """Validate public field types before constructing or saving a run snapshot."""
    for key in ("skip_tuning", "pc_background_scroll", "skip_locked_equipment", "use_stone_cache",
                "initial_stone_check_enabled", "validate_stone_cache"):
        if key in config and not isinstance(config[key], bool):
            raise ValueError(f"{key} 必须是布尔值")
    for key in ("min_level", "initial_stone_min_count"):
        value = config.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise ValueError(f"{key} 必须为非负整数或空值")
    for key in ("skip_start", "target_cell"):
        value = config.get(key)
        if value is not None and (not isinstance(value, (list, tuple)) or len(value) != 2 or any(
                isinstance(number, bool) or not isinstance(number, int) or number < 1 for number in value)):
            raise ValueError("背包位置必须是两个正整数或空值")
    if config.get("scroll_strategy", "") not in {"", "positional"}:
        raise ValueError("背包遍历策略无效")
    for key in ("switches", "smart_tuning_enabled"):
        mapping = config.get(key, {})
        if not isinstance(mapping, dict) or any(not isinstance(value, bool) for value in mapping.values()):
            raise ValueError(f"{key} 必须是布尔值映射")
    rules = config.get("rules", {})
    if not isinstance(rules, dict):
        raise ValueError("规则选择必须是对象")
    for selection in rules.values():
        if not isinstance(selection, dict) or not isinstance(selection.get("enabled", False), bool):
            raise ValueError("规则启用值必须是布尔值")
        names = selection.get("playstyles")
        if names is not None and (not isinstance(names, list) or any(not isinstance(name, str) for name in names)):
            raise ValueError("玩法选择必须是名称列表")


def prepare_tuning(config: dict) -> TuningRunContext:
    config = deepcopy(config)
    validate_run_fields(config)
    slots = config.get("selected_slots") or []
    if not slots:
        raise ValueError("请至少选择一个调律部位")
    from ...config.tune_slots import DEFAULT_SLOTS
    if any(slot not in DEFAULT_SLOTS for slot in slots):
        raise ValueError("调律部位无效")
    enabled = {k: v for k, v in config.get("rules", {}).items()
               if isinstance(v, dict) and v.get("enabled")}
    if not enabled:
        raise ValueError("请至少选择一个调律规则")
    rules = get_tuning_rules()
    switches = {str(k): bool(v) for k, v in config.get("switches", {}).items()}
    judges = []
    configs = {}
    for key, value in enabled.items():
        if not is_rule_implemented(key):
            continue
        rule = rules[key]
        chosen = value.get("playstyles")
        if rule.playstyles and chosen is not None and not chosen:
            raise ValueError(f"规则「{rule.name}」需至少勾选一个玩法")
        if chosen is not None and any(name not in rule.playstyles for name in chosen):
            raise ValueError(f"规则「{rule.name}」包含无效玩法")
        configs[key] = {**value, "switches": switches}
        judges.append(get_tuning_judge(key, configs[key]))
    if not judges:
        raise ValueError("选中的规则均未实现判定逻辑")
    group_key = config.get("base_group", "")
    group = get_tuning_group(group_key)
    if group is None:
        raise ValueError("基础规则组不存在")
    minimum = config.get("initial_stone_min_count")
    stone_check = bool(config.get("initial_stone_check_enabled", minimum is not None))
    minimum = int(minimum) if minimum is not None else (80 if stone_check else None)
    return TuningRunContext(
        selected_slots=list(slots), rule_judges=judges, judge_configs=configs,
        judge_rule_keys=list(configs), base_group=deepcopy(group),
        skip_tuning=bool(config.get("skip_tuning", False)),
        pc_background_scroll=bool(config.get("pc_background_scroll", False)),
        skip_locked_equipment=bool(config.get("skip_locked_equipment", True)),
        use_stone_cache=bool(config.get("use_stone_cache", True)),
        initial_stone_check_enabled=stone_check,
        initial_stone_min_count=minimum if stone_check else None,
        validate_stone_cache=bool(config.get("validate_stone_cache", False)),
        scroll_strategy="positional" if config.get("scroll_strategy") == "positional" else "",
        skip_start=config.get("skip_start"), target_cell=config.get("target_cell"),
        min_level=config.get("min_level"),
        smart_tuning_enabled=bool(config.get("smart_tuning_enabled", {}).get(group_key, False)),
    )
