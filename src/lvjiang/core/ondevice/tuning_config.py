"""设备端调律参数配置 — 原生配置页（TuningConfigActivity）的 Python 侧入口

Kotlin 侧不写死任何规则/玩法/部位清单：get_tuning_config() 把可配置项
（规则注册表 + 开关注册表 + 部位常量）与当前保存值合并后一次性返回，
Activity 只负责按 JSON 渲染表单；save_tuning_config() 校验后写入插件
会话（config/session/yysls/session.json）tuning 节 —— 与桌面调律 Tab
同一落点，auto_tuning._ensure_judge_config() 的会话回退路径直接消费，
工作流零改动。

与 task_runner 相同的跨语言约定：对外函数返回 JSON 文本、自己吞异常。
"""
from __future__ import annotations

import json


def get_tuning_config() -> str:
    """可配置项 + 当前保存值合并视图

    Returns:
        JSON 文本::

            {"ok": bool,
             "rules": [{"key", "name", "enabled",
                        "playstyles": [{"name", "summary", "checked"}]}],
             "slot_groups": [{"name",
                              "slots": [{"key", "label", "checked", "locked"}]}],
             "switches": [{"key", "name", "checked"}],
             "error": str}
    """
    try:
        from .plugins import ensure_loaded

        ensure_loaded()  # 规则注册表在 yysls 插件内，未加载时枚举为空

        from ...apps.yysls.evaluator import get_tuning_rules
        from ...apps.yysls.evaluator.tuning_rules import get_tuning_base
        from ...apps.yysls.tune_config import TuneConfig
        from ...apps.yysls.tune_slots import DEFAULT_SLOTS, LOCKED_SLOTS, SLOT_GROUPS

        tc = TuneConfig.load()

        # 规则：无保存值时缺省与桌面一致（仅 huiyi_general 启用，玩法全勾）
        saved_rules: dict = tc.rules or {
            "huiyi_general": {"enabled": True}}
        rules = []
        for key, rule in get_tuning_rules().items():
            cfg = saved_rules.get(key, {})
            selected = cfg.get("playstyles")  # None = 缺省全勾
            rules.append({
                "key": key,
                "name": rule.name,
                "enabled": bool(cfg.get("enabled", False)),
                "playstyles": [
                    {"name": name, "summary": summary,
                     "checked": selected is None or name in selected}
                    for name, summary in rule.playstyle_options.items()
                ],
            })

        selected_slots = tc.selected_slots or list(DEFAULT_SLOTS)
        slot_groups = [
            {
                "name": group_name,
                "slots": [
                    {"key": slot_key, "label": slot_label,
                     "checked": slot_key not in LOCKED_SLOTS
                     and slot_key in selected_slots,
                     "locked": slot_key in LOCKED_SLOTS}
                    for slot_key, slot_label in slots
                ],
            }
            for group_name, slots in SLOT_GROUPS
        ]

        saved_switches = tc.switches
        switches = [
            {"key": key, "name": name,
             "checked": bool(saved_switches.get(key, False))}
            for key, name in get_tuning_base().switches.items()
        ]

        return json.dumps(
            {"ok": True, "rules": rules, "slot_groups": slot_groups,
             "switches": switches, "error": ""},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps(
            {"ok": False, "rules": [], "slot_groups": [], "switches": [],
             "error": f"{type(e).__name__}: {e}"},
            ensure_ascii=False,
        )


def save_tuning_config(payload: str) -> str:
    """校验并保存调律配置

    Args:
        payload: JSON 对象文本::

            {"selected_slots": [部位 key],
             "rules": {规则 key: {"enabled": bool, "playstyles": [玩法名]}},
             "switches": {开关 key: bool}}

    Returns:
        JSON 文本 ``{"ok": bool, "message": str}``。
        校验规则与桌面 _start_tuning 一致，message 可直接 toast。
    """
    try:
        from .plugins import ensure_loaded

        ensure_loaded()

        from ...apps.yysls.evaluator import get_tuning_rules
        from ...apps.yysls.tune_config import TuneConfig
        from ...apps.yysls.tune_slots import LOCKED_SLOTS, SLOT_LABELS

        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("payload 必须是 JSON 对象")

        # 部位：只认已知且未禁用的 key，保持常量声明顺序
        raw_slots = set(data.get("selected_slots") or [])
        selected_slots = [
            key for key in SLOT_LABELS
            if key in raw_slots and key not in LOCKED_SLOTS
        ]
        if not selected_slots:
            return json.dumps(
                {"ok": False, "message": "请至少选择一个调律部位"},
                ensure_ascii=False,
            )

        # 规则：按注册表重建，来路不明的 key / 玩法名直接丢弃
        raw_rules = data.get("rules")
        raw_rules = raw_rules if isinstance(raw_rules, dict) else {}
        rules_cfg: dict[str, dict] = {}
        enabled_count = 0
        for key, rule in get_tuning_rules().items():
            cfg = raw_rules.get(key)
            cfg = cfg if isinstance(cfg, dict) else {}
            entry: dict = {"enabled": bool(cfg.get("enabled", False))}
            declared = list(rule.playstyle_options)
            if declared:
                selected = cfg.get("playstyles")
                entry["playstyles"] = (
                    declared if selected is None
                    else [name for name in declared if name in selected]
                )
                if entry["enabled"] and not entry["playstyles"]:
                    return json.dumps(
                        {"ok": False,
                         "message": f"规则「{rule.name}」需至少勾选一个玩法"},
                        ensure_ascii=False,
                    )
            if entry["enabled"]:
                enabled_count += 1
            rules_cfg[key] = entry
        if not enabled_count:
            return json.dumps(
                {"ok": False, "message": "请至少选择一个调律规则"},
                ensure_ascii=False,
            )

        raw_switches = data.get("switches")
        raw_switches = raw_switches if isinstance(raw_switches, dict) else {}
        switches = {str(k): bool(v) for k, v in raw_switches.items()}

        # skip_tuning 不进设备端 UI，保留会话原值（缺省 False）
        old = TuneConfig.load()
        TuneConfig(
            selected_slots=selected_slots,
            rules=rules_cfg,
            switches=switches,
            skip_tuning=old.skip_tuning,
            skip_start=old.skip_start,
            target_cell=old.target_cell,
            scroll_strategy=old.scroll_strategy,
        ).save()
        return json.dumps({"ok": True, "message": "已保存"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps(
            {"ok": False, "message": f"{type(e).__name__}: {e}"},
            ensure_ascii=False,
        )
