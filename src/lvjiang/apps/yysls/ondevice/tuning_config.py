"""设备端调律参数配置 — 原生配置页（TuningConfigActivity）的 Python 侧入口

Kotlin 侧不写死任何规则/玩法/部位清单：get_tuning_config() 把可配置项
（规则注册表 + 开关注册表 + 部位常量）与当前保存值合并后一次性返回，
Activity 只负责按 JSON 渲染表单；save_tuning_config() 校验后写入统一存储
wf_configs["auto_tuning"] —— 与桌面调律 Tab 同一落点，
auto_tuning._ensure_judge_config() 的回退路径直接消费，工作流零改动。

与 task_runner 相同的跨语言约定：对外函数返回 JSON 文本、自己吞异常。
"""
from __future__ import annotations

import json

from ....i18n import tr


def get_tuning_config() -> str:
    """可配置项 + 当前保存值合并视图

    Returns:
        JSON 文本::

            {"ok": bool,
             "base_groups": [{"key", "name", "checked"}],
             "rules": [{"key", "name", "enabled",
                        "playstyles": [{"name", "summary", "checked", "switch"}]}],
             "slot_groups": [{"name",
                              "slots": [{"key", "label", "checked", "locked"}]}],
             "switches": [{"key", "name", "checked"}],
             "error": str}
    """
    try:
        from ....core.ondevice.plugins import ensure_loaded

        ensure_loaded(("yysls",))

        from ....core.config.wf_configs import get_wf_config
        from ..config.tune_slots import (
            DEFAULT_SLOTS,
            LOCKED_SLOTS,
            SLOT_GROUPS,
        )
        from ..core.evaluator import get_tuning_rules
        from ..core.tuning_rules import (
            get_tune_config,
            get_tuning_group_manager,
        )

        tc = get_wf_config("auto_tuning")

        # 基础规则组：单选，无持久值时取第一个可用
        group_key = tc.get("base_group", "")
        groups = get_tuning_group_manager().get_groups()
        if group_key not in groups:
            group_key = next(iter(groups), "")
        base_groups = [
            {"key": key, "name": group.name,
             "description": group.description,
             "checked": key == group_key}
            for key, group in get_tuning_group_manager().get_groups().items()
        ]

        # 规则：无保存值时缺省与桌面一致（仅 huiyi_general 启用，玩法全勾）
        saved_rules: dict = tc.get("rules") or {
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
                     "checked": selected is None or name in selected,
                     "switch": rule.playstyles[name].switch}
                    for name, summary in rule.playstyle_options.items()
                ],
            })

        selected_slots = tc.get("selected_slots") or list(DEFAULT_SLOTS)
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

        saved_switches = tc.get("switches", {})
        switches = [
            {"key": key, "name": name,
             "checked": bool(saved_switches.get(key, False))}
            for key, name in get_tune_config().switches.items()
        ]

        return json.dumps(
            {"ok": True, "base_groups": base_groups, "rules": rules,
             "slot_groups": slot_groups,
             "switches": switches, "error": ""},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps(
            {"ok": False, "base_groups": [], "rules": [], "slot_groups": [],
             "switches": [],
             "error": f"{type(e).__name__}: {e}"},
            ensure_ascii=False,
        )


def save_tuning_config(payload: str) -> str:
    """校验并保存调律配置

    Args:
        payload: JSON 对象文本::

            {"base_group": 规则组 key（可缺省）,
             "selected_slots": [部位 key],
             "rules": {规则 key: {"enabled": bool, "playstyles": [玩法名]}},
             "switches": {开关 key: bool}}

    Returns:
        JSON 文本 ``{"ok": bool, "message": str}``。
        校验规则与桌面 _start_tuning 一致，message 可直接 toast。
    """
    try:
        from ....core.ondevice.plugins import ensure_loaded

        ensure_loaded(("yysls",))

        from ....core.config.wf_configs import get_wf_config, update_wf_config
        from ..config.tune_slots import LOCKED_SLOTS, SLOT_LABELS
        from ..core.evaluator import get_tuning_rules
        from ..core.tuning_rules import (
            get_tuning_group_manager,
        )

        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError(tr("payload 必须是 JSON 对象"))

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

        # 桌面端调试/后台参数不进设备端 UI，update_wf_config 合并时保留原值
        old = get_wf_config("auto_tuning")

        # 基础规则组：按注册表校验，非法/缺省回退原值
        raw_group = data.get("base_group")
        group_keys = get_tuning_group_manager().get_groups()
        if isinstance(raw_group, str) and raw_group in group_keys:
            base_group = raw_group
        else:
            base_group = old.get("base_group", "")
        update_wf_config("auto_tuning", {
            "selected_slots": selected_slots,
            "rules": rules_cfg,
            "switches": switches,
            "base_group": base_group,
        })
        return json.dumps({"ok": True, "message": tr("已保存")}, ensure_ascii=False)
    except Exception as e:
        return json.dumps(
            {"ok": False, "message": f"{type(e).__name__}: {e}"},
            ensure_ascii=False,
        )
