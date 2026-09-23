"""备战方案字段之间的联动规则。

流派、两门武学和玩法不是三个独立字段：流派决定武学，武学决定玩法候选。这套
规则被两个界面共用——新建方案对话框，以及方案管理表格里的逐列编辑。规则只写
这一份，布线各写各的；两边各实现一遍迟早漂移，而下面第三条尤其容易漏。
"""

from __future__ import annotations

from .models import LoadoutPlan


def school_arts(schools: dict, school: str) -> tuple[str, str]:
    """流派预置的（主武学, 副武学）；流派不存在时返回两个空串。

    流派是填武学的便捷入口，本身不是方案上持久化的字段——方案只存两门武学，
    流派由 ``resolve_school`` 反查得到。
    """
    config = schools.get(str(school or "").strip()) or {}
    return (
        str((config.get("main") or {}).get("martial_art") or ""),
        str((config.get("sub") or {}).get("martial_art") or ""),
    )


def matches_school_arts(schools: dict, school: str,
                        main_art: str, sub_art: str) -> bool:
    """这两门武学是否仍是该流派的预置组合（无序比较）。

    主副只是槽位称呼，不规定顺序，所以按集合比。手动把武学改成别的组合之后
    就不再算绑定该流派，界面应转入自由选武学模式。
    """
    configured = set(school_arts(schools, school))
    return bool(school) and configured == {main_art, sub_art}


def playstyle_options(
    game_config,
    main_art: str,
    sub_art: str,
    *,
    school: str = "",
    plan: LoadoutPlan | None = None,
) -> list[tuple[str, str]]:
    """玩法下拉的候选，返回 ``[(展示文案, 稳定值)]``，首项恒为「不选择玩法」。

    三条规则：

    1. 按两门武学**无序**匹配——主副只是槽位称呼，交换不该改变候选；
    2. 绑定流派时再按流派收窄；
    3. ``plan`` 给出正在编辑的方案时，若它已选的玩法不在候选内、而武学又没被
       改动过，就把该玩法保留在列表里并标注「当前不匹配」。少了这条，仅仅因为
       玩法配置改过就会在用户下一次编辑别的字段时把它的玩法静默清空。
    """
    from .....i18n import tr

    names = list(game_config.get_playstyles_for_arts([main_art, sub_art]))
    if school:
        names = [name for name in names
                 if (game_config.get_playstyle(name) or {}).get(
                     "school") == school]
    options = [(tr("不选择玩法"), "")]
    options.extend((name, name) for name in names)
    if (plan is not None and plan.playstyle and plan.playstyle not in names
            and {main_art, sub_art}
            == {plan.main_martial_art, plan.sub_martial_art}):
        options.append(
            (tr("{name}（当前不匹配）").format(name=plan.playstyle),
             plan.playstyle))
    return options
