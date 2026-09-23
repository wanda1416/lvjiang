"""The only workflow write surface for user equipment/loadout data."""

from loguru import logger

from lvjiang.workflows.builtins._registry import builtin_func


def _repository(_engine):
    from ...core.loadout import LoadoutRepository
    username = getattr(_engine, "run_username", "") or "default"
    return LoadoutRepository(username, getattr(_engine, "users_dir", None))


@builtin_func("scanned_loadout_names")
def _scanned_loadout_names(_engine) -> dict[str, bool]:
    """Snapshot existing local plan names before a batch scans game plans."""
    return {plan.name: True for plan in _repository(_engine).load().plans.values()}


def _match_playstyle(name: str, candidates: list[str],
                     playstyles: dict[str, dict]) -> str:
    """按方案名从候选玩法里挑一个。

    同一流派同一武学组合下可能并存多个玩法（牵丝·霖的火拳和纯奶武学完全
    相同），方案名未必写着玩法名，只靠名字包含会落空并静默取第一个候选。

    三级判定：

    1. 方案名里直接出现玩法名——最具体的信号，优先；
    2. 命中玩法配置的匹配关键字，**取最长的那条**。方案名「输出奶」会同时
       命中火拳的「输出」和纯奶的「奶」，取长的才能选中火拳；按声明顺序就
       不一定对。长度相同时按配置声明顺序，并记一条日志提醒配置冲突。
    3. 都落空时回落第一个候选，保持原有行为。
    """
    if not candidates:
        return ""
    for style in candidates:
        if style in name:
            return style
    best_style, best_keyword = "", ""
    for style in candidates:
        for keyword in (playstyles.get(style) or {}).get("match_keywords") or []:
            if keyword not in name or len(keyword) < len(best_keyword):
                continue
            if best_keyword and len(keyword) == len(best_keyword):
                logger.warning(
                    f"备战方案 {name!r} 同时命中等长关键字 {best_keyword!r} 与 "
                    f"{keyword!r}，按配置顺序取 {best_style!r}")
                continue
            best_style, best_keyword = style, keyword
    if best_style:
        logger.info(
            f"备战方案 {name!r} 按关键字 {best_keyword!r} 匹配玩法 {best_style!r}")
        return best_style
    return candidates[0]


@builtin_func("ensure_scanned_loadout")
def _ensure_scanned_loadout(
    _engine, name: str, main_art: str, sub_art: str,
) -> dict[str, str | bool]:
    """Match a game plan by name, or create it without changing the active plan."""
    from ...config import get_game_config
    from ...core.loadout import resolve_school
    from ...core.loadout.models import COMBAT_TYPE_PVE, COMBAT_TYPE_PVP

    name, main_art, sub_art = (str(value or "").strip()
                               for value in (name, main_art, sub_art))
    if not name or not main_art or not sub_art or main_art == sub_art:
        raise ValueError("游戏方案名称或两门武学识别不完整，不能创建备战方案")
    repo = _repository(_engine)
    matches = [plan for plan in repo.load().plans.values() if plan.name == name]
    if len(matches) > 1:
        return {"ok": False, "reason": "本地存在同名备战方案，无法确定写入目标"}
    if matches:
        plan = matches[0]
        if {plan.main_martial_art, plan.sub_martial_art} != {main_art, sub_art}:
            return {"ok": False, "reason": "本地同名方案的武学与游戏不一致"}
        return {"ok": True, "name": name,
                "main_art": main_art, "sub_art": sub_art,
                "playstyle": plan.playstyle, "created": False}

    game_config = get_game_config()
    school = resolve_school(main_art, sub_art, game_config.get_schools())
    arts = {main_art, sub_art}
    # get_playstyles() 保留配置声明顺序；UI 候选接口按名称排序，不能用于默认项。
    playstyles = game_config.get_playstyles()
    candidates = [style for style, definition in playstyles.items()
                  if set(definition.get("arts") or []) == arts
                  and (not school or definition.get("school") == school)]
    playstyle = _match_playstyle(name, candidates, playstyles)
    # 游戏里 PVP 方案通常就叫「xxPVP」；照名字先定下来，省得用户扫完再逐个
    # 去改。认不出就按 PVE——绝大多数方案是 PVE，猜错的代价也只是少过滤一条。
    combat_type = (COMBAT_TYPE_PVP if "pvp" in name.lower()
                   else COMBAT_TYPE_PVE)
    repo.create_plan(name, main_art, sub_art, playstyle=playstyle,
                     combat_type=combat_type, activate=False)
    logger.info(
        f"已从游戏新建备战方案: {name}，流派={school or '-'}，"
        f"玩法={playstyle or '-'}，对战类型={combat_type}")
    return {"ok": True, "name": name,
            "main_art": main_art, "sub_art": sub_art,
            "playstyle": playstyle, "created": True}


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
    fp = repo.assign_equipment(plan_id, slot_key, equip_dict, scanned=True)
    _notify_equipment_changed(_engine)
    return fp


@builtin_func("set_scanned_loadout_gongjue")
def _set_scanned_loadout_gongjue(_engine, gongjue: str) -> str:
    """Set the scanned plan's bow-jue set without changing the UI active plan."""
    if gongjue not in ("会意", "会心", "精准"):
        raise ValueError(f"无法识别的弓玦套装: {gongjue!r}")
    plan_id = _engine.context.get("_bound_loadout_plan_id")
    if not plan_id:
        raise ValueError("写入弓玦前必须通过方案名称与武学绑定写入目标")
    repo = _repository(_engine)
    repo.configure_plan(plan_id, gongjue=gongjue)
    _notify_equipment_changed(_engine)
    logger.info(f"已更新扫描方案的弓玦套装: {gongjue}")
    return gongjue
