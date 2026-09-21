"""The only workflow write surface for user equipment/loadout data."""

from loguru import logger

from lvjiang.workflows.builtins._registry import builtin_func


def _repository(_engine):
    from ...core.loadout import LoadoutRepository
    username = getattr(_engine, "run_username", "") or "default"
    return LoadoutRepository(username, getattr(_engine, "users_dir", None))


@builtin_func("loadout_scan_targets")
def _loadout_scan_targets(_engine) -> list[dict[str, str]]:
    """Freeze this user's configured plans in their visible order for one run."""
    state = _repository(_engine).load()
    targets = [
        {
            "name": state.plans[pid].name,
            "main_art": state.plans[pid].main_martial_art,
            "sub_art": state.plans[pid].sub_martial_art,
            "playstyle": state.plans[pid].playstyle,
        }
        for pid in state.ordered_plan_ids()
        if state.plans[pid].main_martial_art and state.plans[pid].sub_martial_art
    ]
    names = [item["name"] for item in targets]
    if len(names) != len(set(names)):
        raise ValueError("备战方案存在重名；游戏只提供名称，不能安全批量扫描")
    return targets


@builtin_func("bind_scanned_loadout")
def _bind_scanned_loadout(_engine, name: str, main_art: str, sub_art: str) -> str:
    """Bind writes to the one local plan matching the verified game name and arts."""
    _engine.context.pop("_bound_loadout_plan_id", None)
    state = _repository(_engine).load()
    matches = [plan for plan in state.plans.values() if plan.name == name]
    if len(matches) != 1:
        raise ValueError(f"备战方案名称 {name!r} 匹配 {len(matches)} 个本地方案，无法安全写入")
    plan = matches[0]
    if sorted((plan.main_martial_art, plan.sub_martial_art)) != sorted((main_art, sub_art)):
        raise ValueError(f"备战方案 {name!r} 的游戏武学与本地配置不一致，停止写入")
    _engine.context["_bound_loadout_plan_id"] = plan.id
    return plan.id


@builtin_func("loadout_scan_target")
def _loadout_scan_target(
    _engine, name: str = "", main_art: str = "", sub_art: str = "",
) -> dict[str, str]:
    """Resolve direct-run parameters without switching the UI active plan."""
    state = _repository(_engine).load()
    if name:
        matches = [plan for plan in state.plans.values() if plan.name == name]
        if len(matches) != 1:
            raise ValueError(f"备战方案名称 {name!r} 匹配 {len(matches)} 个本地方案")
        plan = matches[0]
    else:
        plan = state.active_plan
    if main_art and sub_art and sorted((main_art, sub_art)) != sorted((
            plan.main_martial_art, plan.sub_martial_art)):
        raise ValueError(f"备战方案 {plan.name!r} 的传入武学与本地配置不一致")
    if bool(main_art) != bool(sub_art):
        raise ValueError("主武学和副武学参数必须同时提供")
    return {
        "name": plan.name,
        "main_art": main_art or plan.main_martial_art,
        "sub_art": sub_art or plan.sub_martial_art,
        "playstyle": plan.playstyle,
    }


def _notify_equipment_changed(_engine) -> None:
    """写入成功后经通用 UI 桥发布 yysls 事件。

    无回调时（测试/独立执行端）静默跳过；通知失败仅记日志，
    不影响写入结果。
    """
    callback = getattr(_engine, "_ui_callback", None)
    if callback is None:
        return
    try:
        from lvjiang.apps.yysls.core.events import APP_ID, EQUIPMENT_CHANGED
        from lvjiang.ui.app_events import AppEvent
        callback("app_event", event=AppEvent(APP_ID, EQUIPMENT_CHANGED))
    except Exception as e:
        logger.debug(f"equipment_changed 通知失败（写入已生效）: {e}")


@builtin_func("write_bag_item")
def _write_bag_item(_engine, group_key: str, equip_dict: dict) -> str:
    if not isinstance(equip_dict, dict):
        raise ValueError("装备数据必须是字典")
    from ...config import get_game_config
    expected = get_game_config().get_type_to_group().get(equip_dict.get("type", ""))
    if expected and expected != group_key:
        raise ValueError(f"装备类型与分组不匹配: {group_key} != {expected}")
    fp = _repository(_engine).upsert_item(equip_dict)
    _notify_equipment_changed(_engine)
    return fp


@builtin_func("write_equipped")
def _write_equipped(_engine, slot_key: str, equip_dict: dict) -> str:
    repo = _repository(_engine)
    plan_id = _engine.context.get("_bound_loadout_plan_id")
    if not plan_id:
        raise ValueError("扫描装备前必须通过方案名称与武学绑定写入目标")
    fp = repo.assign_equipment(plan_id, slot_key, equip_dict)
    _notify_equipment_changed(_engine)
    return fp
