"""内置函数 - 角色基础属性 OCR 解析与"创建基础属性"面板预填触发"""

from loguru import logger

from lvjiang.workflows.builtins._registry import builtin_func


@builtin_func("save_scanned_base_attrs")
def _save_scanned_base_attrs(_engine, prefill: dict) -> str:
    """Silently store the real base values for the bound plan's school/playstyle."""
    from ...config import get_game_config, save_play_style
    from ...core.combat.base_attribute_ingest import (
        derive_base_attributes,
        stored_base_fields,
    )
    from ...core.combat.combat_attrs import COMBAT_ATTR_FIELDS, CombatAttributes
    from ...core.graduation.context import gongjue_attrs
    from ...core.loadout import LoadoutRepository, resolve_school

    plan_id = _engine.context.get("_bound_loadout_plan_id")
    if not plan_id:
        raise ValueError("尚未绑定经过验证的备战方案，不能静默写入基础属性")
    required = {"min_outer", "max_outer", "precision", "crit_rate"}
    if (not isinstance(prefill, dict) or not required <= prefill.keys()
            or prefill.get("_right_outer_valid") is not True
            or prefill.get("_right_attr_attack_valid") is not True
            or prefill.get("_right_outer_pen_valid") is not True
            or prefill.get("_right_attr_pen_valid") is not True):
        raise ValueError("角色属性右侧详情识别不完整，拒绝静默覆盖基础属性")
    required_bonus = {"crit_dmg", "intent_dmg", "outer_bonus", "attr_bonus_current"}
    missing_bonus = required_bonus - prefill.keys()
    if missing_bonus:
        raise ValueError(
            f"角色面板增减伤属性识别不完整（缺少 {', '.join(sorted(missing_bonus))}），"
            "拒绝静默覆盖基础属性"
        )
    username = getattr(_engine, "run_username", "")
    if not username:
        raise ValueError("任务未绑定用户，不能静默写入基础属性")
    repo = LoadoutRepository(username, getattr(_engine, "users_dir", None))
    state = repo.load()
    plan = state.plans.get(plan_id)
    if plan is None or not plan.playstyle:
        raise ValueError("备战方案不存在或未配置玩法，不能命名基础属性")
    game_config = get_game_config()
    school = resolve_school(plan.main_martial_art, plan.sub_martial_art,
                            game_config.get_schools())
    if not school:
        raise ValueError(f"备战方案 {plan.name!r} 的武学无法解析流派")
    school_attr = game_config.get_school_attr(school)
    if not school_attr:
        raise ValueError(f"流派 {school!r} 没有属性映射")
    from ...core.combat.combat_attrs import SCHOOL_ATTR_FIELD_MAP
    mapping = SCHOOL_ATTR_FIELD_MAP.get(school_attr, {})
    required_school = {
        mapping.get("min_attr"), mapping.get("max_attr"),
        mapping.get("attr_pen"),
    }
    if not required_school <= prefill.keys():
        raise ValueError(f"流派 {school!r} 的属性攻击或穿透识别不完整")
    # OCR 返回百分数的面板写法（如 110.1 表示 110.1%）；战斗模型和装备贡献
    # 使用小数（1.101）。与手动创建表单的 get_panel_attrs 保持同一单位。
    panel_values = dict(prefill)
    bonus_field = mapping.get("attr_bonus")
    if bonus_field:
        panel_values.setdefault(bonus_field, panel_values["attr_bonus_current"])
    for field, _, unit, _ in COMBAT_ATTR_FIELDS:
        if unit == "%" and field in panel_values:
            panel_values[field] = float(panel_values[field]) / 100.0
    base = derive_base_attributes(
        CombatAttributes.from_dict(panel_values), state.resolved_equipment(plan_id),
        gongjue_attrs(plan.gongjue))
    values = stored_base_fields(school_attr, base)
    name = f"{username}_{plan.name}"
    if not values:
        raise ValueError(f"基础属性 {name!r} 反推结果为空，拒绝写入")
    save_play_style(school, name, values)
    repo.configure_plan(plan_id, base_attribute=name)
    from .equipment_ingest import _notify_equipment_changed
    _notify_equipment_changed(_engine)
    logger.info(f"基础属性已静默写入: {school}/{name}（方案 {plan.name}）")
    return name


@builtin_func("to_role_base_attrs")
def _to_role_base_attrs(raw: dict) -> dict:
    """解析角色详情页滚动识别的 OCR 原始数据为基础属性字典

    raw 由 scan_role_base_attr.wf 暂存：{"left_1": ..., "left_2": ...,
    "right_outer_attack": ..., "right_attr_attack": ...,
    "right_outer_pen": ..., "right_attr_pen": ...}。
    返回字段名对齐 combat_attrs.COMBAT_ATTR_FIELDS 的 flat dict，
    可直接交给 open_base_attr_form 预填"创建基础属性"面板。

    .wf 用法:
        eval $parsed = to_role_base_attrs($data)
        eval open_base_attr_form($parsed)
    """
    if not isinstance(raw, dict) or not raw:
        logger.warning("to_role_base_attrs: 输入为空或非字典")
        return {}

    from ...core.role_attr_parser import get_role_attr_parser

    try:
        return get_role_attr_parser().parse(raw)
    except Exception as e:
        logger.warning(f"to_role_base_attrs: 解析失败: {e}")
        return {}


@builtin_func("open_base_attr_form")
def _open_base_attr_form(_engine, prefill: dict) -> None:
    """触发 UI 弹出"创建基础属性"面板并预填数值，不等待用户确认

    经通用 AppEvent 桥广播给 yysls 基础属性页签，不阻塞工作流线程。

    .wf 用法:
        eval open_base_attr_form($parsed)
    """
    callback = getattr(_engine, "_ui_callback", None)
    if callback is None:
        logger.debug("open_base_attr_form: 无 UI 回调（测试/独立执行端），跳过")
        return
    try:
        from lvjiang.apps.yysls.core.events import APP_ID, OPEN_PLAY_STYLE_FORM
        from lvjiang.ui.app_events import AppEvent
        callback(
            "app_event",
            event=AppEvent(APP_ID, OPEN_PLAY_STYLE_FORM, prefill or {}),
        )
    except Exception as e:
        logger.warning(f"open_base_attr_form: 触发失败: {e}")
