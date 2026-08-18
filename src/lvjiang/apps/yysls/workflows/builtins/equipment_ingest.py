"""The only workflow write surface for user equipment/loadout data."""

from loguru import logger

from lvjiang.workflows.builtins._registry import builtin_func


def _repository(_engine):
    from ...core.loadout import LoadoutRepository
    username = getattr(_engine, "run_username", "") or "default"
    return LoadoutRepository(username)


def _notify_equipment_changed(_engine) -> None:
    """写入成功后经 UI 桥在主线程发射 equipment_changed 信号。

    无回调时（测试/独立执行端）静默跳过；通知失败仅记日志，
    不影响写入结果。
    """
    callback = getattr(_engine, "_ui_callback", None)
    if callback is None:
        return
    try:
        callback("equipment_changed")
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
        plan_id = repo.load().active_plan_id
        _engine.context["_bound_loadout_plan_id"] = plan_id
    fp = repo.assign_equipment(plan_id, slot_key, equip_dict)
    _notify_equipment_changed(_engine)
    return fp
