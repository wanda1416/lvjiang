#!/usr/bin/env python3
"""一次性对拍工具：用 PC 端实现给网页端毕业率算一组期望值。

用例数据是 ``server/web/scripts/graduation-fixture.json`` 里的合成装备与
基础属性（不含任何用户真实数据）。本脚本跑 PC 端链路（装备归一化 → 属性
聚合 → 套抗性 → 模型程序），把结果写进
``server/web/scripts/graduation-expected.json``；网页端
``scripts/check-graduation.ts`` 用同一份用例跑 TS 实现并与该文件比对。

模型或合成用例变了以后重新生成一次：

    python scripts/one_off/graduation_web_parity.py

这不是常驻回归：它只在「网页实现是否仍与 PC 同结果」这条一次性等价性
验证上使用，日常保护由网页端脚本自己完成。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from lvjiang.apps.yysls.config import get_game_config  # noqa: E402
from lvjiang.apps.yysls.core.affix_cap import (  # noqa: E402
    affix_dict_cap_pct,
    equip_affix_cap_pcts,
)
from lvjiang.apps.yysls.core.combat.combat_attrs import (  # noqa: E402
    CombatAttributes,
    GraduationAttrContext,
    apply_hypothetical_caps,
)
from lvjiang.apps.yysls.core.graduation import (  # noqa: E402
    get_graduation_calculator,
)
from lvjiang.apps.yysls.core.graduation.context import gongjue_attrs  # noqa: E402
from lvjiang.apps.yysls.core.graduation.scoring import graduation_input  # noqa: E402
from lvjiang.apps.yysls.core.loadout.models import resolve_school  # noqa: E402

_FIXTURE = _REPO_ROOT / "server/web/scripts/graduation-fixture.json"
_EXPECTED = _REPO_ROOT / "server/web/scripts/graduation-expected.json"


def _load_fixture() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _equipped(fixture: dict) -> dict[str, dict]:
    plan = fixture["plan"]
    pool = fixture["equipment_items"]
    resolved: dict[str, dict] = {}
    for slot, fingerprint in plan["equipment"].items():
        item = json.loads(json.dumps(pool[fingerprint]))
        plan_set = (plan.get("equipment_sets") or {}).get(slot)
        if plan_set:
            item["equipment_set"] = plan_set
        resolved[slot] = item
    return resolved


def _case(fixture: dict, assumptions: dict) -> dict:
    gc = get_game_config()
    plan = fixture["plan"]
    level = int(fixture["world_level"])
    school = resolve_school(
        plan["main_martial_art"], plan["sub_martial_art"], gc.get_schools())
    if not school:
        raise SystemExit("用例主副武学匹配不到流派")
    base_data = fixture["play_styles"][school][plan["base_attribute"]]
    base = CombatAttributes.from_dict(base_data) + gongjue_attrs(
        plan["gongjue"], gc, world_level=level,
        gongjue_level=plan.get("gongjue_level") or None)
    context = GraduationAttrContext.from_school(school, world_level=level)
    projected = apply_hypothetical_caps(
        _equipped(fixture),
        full_chengyin=bool(assumptions["fullChengyin"]),
        full_dingyin=bool(assumptions["fullDingyin"]),
        full_level=int(assumptions["fullLevel"]),
        playstyle=plan["playstyle"],
        simulate_transmute=False,
    )
    attrs = graduation_input(base, projected, school, gc, attr_context=context)
    calculator = get_graduation_calculator(
        school, plan["graduation_scheme"], level)
    if calculator is None:
        raise SystemExit("用例方案没有可用模型")
    result = calculator.calculate(attrs)
    return {
        "school": school,
        "model_level": calculator.model.level,
        "model_version": calculator.model.version,
        "world_level": level,
        "dps": result.dps,
        "baseline": result.baseline_dps,
        "rate": result.graduation_rate,
        "combat_time": result.combat_time,
        "attrs": attrs.to_dict(),
        "projected_equipment": projected,
    }


def _cards(fixture: dict) -> dict:
    """装备卡片与属性卡片的展示口径（对齐 PC cards.py / attrs_tab.py）。

    卡片走的是**未投影**的装备（与 PC 装备页一致：假设只影响属性与毕业率，
    不改卡片上的数值），属性走**投影后**的结果。
    """
    from lvjiang.apps.yysls.core.combat.combat_attrs import (
        fold_wuxiang_pen,
        format_value,
    )
    from lvjiang.apps.yysls.core.graduation.scoring import equipment_attrs

    gc = get_game_config()
    plan = fixture["plan"]
    level = int(fixture["world_level"])
    school = resolve_school(
        plan["main_martial_art"], plan["sub_martial_art"], gc.get_schools())
    equipped = _equipped(fixture)

    cards: dict[str, dict] = {}
    for slot, item in equipped.items():
        cap_pcts = equip_affix_cap_pcts(item, game_config=gc)
        item_level = item.get("level")
        affixes = []
        for index in range(1, 6):
            affix = item.get(f"affix_{index}")
            if not isinstance(affix, dict) or not affix.get("name"):
                continue
            affixes.append({
                "name": affix["name"],
                "value": affix.get("value"),
                "unit": affix.get("unit") or "",
                "cap_pct": affix_dict_cap_pct(affix, item_level, game_config=gc),
                "is_transferred": bool(affix.get("is_transferred")),
            })
        dingyin = item.get("dingyin") or {}
        cards[slot] = {
            "name": item.get("name"),
            "level": item_level,
            "is_chengyin": bool(item.get("is_chengyin")),
            "level_pct": (sum(cap_pcts) / len(cap_pcts)) if cap_pcts else None,
            "affixes": affixes,
            "dingyin": (
                {
                    "name": dingyin.get("name"),
                    "value": dingyin.get("value"),
                    "unit": dingyin.get("unit") or "%",
                    "cap_pct": affix_dict_cap_pct(
                        {**dingyin, "unit": dingyin.get("unit") or "%"},
                        item_level, game_config=gc),
                }
                if dingyin.get("name") else None
            ),
        }

    base_data = fixture["play_styles"][school][plan["base_attribute"]]
    base = CombatAttributes.from_dict(base_data) + gongjue_attrs(
        plan["gongjue"], gc, world_level=level,
        gongjue_level=plan.get("gongjue_level") or None)
    context = GraduationAttrContext.from_school(school, world_level=level)
    raw_attrs = base + fold_wuxiang_pen(
        equipment_attrs(equipped, gc), context.target_pen_field)
    effective = graduation_input(base, equipped, school, gc, attr_context=context)
    effective_items = {label: value for label, value, _ in effective.display_items()}
    combat = [
        {"label": label, "raw": format_value(value, unit),
         "effective": format_value(effective_items.get(label, value), unit)}
        for label, value, unit in raw_attrs.display_items()
    ]
    return {"school_attr": gc.get_school_attr(school) or "", "slots": cards,
            "combat": combat}


def main() -> int:
    fixture = _load_fixture()
    cases = {
        item["name"]: _case(fixture, item["assumptions"])
        for item in fixture["assumption_sets"]
    }
    payload = {
        "_generated_by": "scripts/one_off/graduation_web_parity.py",
        "_fixture": "server/web/scripts/graduation-fixture.json",
        "cases": cases,
        "display": _cards(fixture),
    }
    _EXPECTED.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name, case in cases.items():
        print(f"{name}: dps={case['dps']:.6f} rate={case['rate']:.9f}",
              file=sys.stderr)
    print(f"写入 {_EXPECTED}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
