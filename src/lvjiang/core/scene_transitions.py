"""页面切换契约：area 声明"点了到哪去"，据此校验视图的入口与转移。

声明放在**场景定义**（游戏语义，不随布局变），可用性放在**布局**
（``Region.disabled`` 表达"这个布局上没有这条边"）。这个分工让
"桌面端按 B 一步到背包、安卓端要 主页→菜单→背包"这类拓扑差异不必污染语义层。

契约只做声明和校验，**不驱动执行**：现有 wf 与 Python 编排一行都不用改，
契约先行、逐步补全，后续读代码的人（和 AI）能直接看懂转移逻辑。
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from .scene_definition_models import BASE_VIEW_KEY


@dataclass(frozen=True)
class Transition:
    """一条页面切换边。"""

    from_scene: str
    from_view: str          # 触发按钮所在视图（"" = 基底）
    entity: str             # 触发按钮 key
    to_scene: str           # 目标场景
    to_view: str            # 目标视图（"" = 基底）

    navigation: str = ""
    definition_scene: str = ""

    @property
    def is_return(self) -> bool:
        return self.to_scene == "@caller"

    @property
    def is_internal(self) -> bool:
        """本场景内换视图。"""
        return self.from_scene == self.to_scene


def parse_target(raw: str, current_scene: str) -> tuple[str, str] | None:
    """解析 ``to:`` 的目标。

    - ``"equip_tune_detail"``        → 该场景基底视图
    - ``"equip_tune_detail/result"`` → 该场景的 result 视图
    - ``"/result"``                  → 本场景切到 result 视图

    格式非法返回 ``None``。
    """
    text = str(raw or "").strip()
    if not text:
        return None
    if text == "@caller":
        return "@caller", ""
    if text.startswith("@") or text.count("/") > 1:
        return None
    scene, _, view = text.partition("/")
    scene = scene.strip() or current_scene
    view = view.strip()
    if not scene:
        return None
    return scene, view


def effective_entities(scenes: dict, scene):
    """引用的动态返回在使用方上下文解析，固定相对目标仍属于定义方。"""
    yield from (*scene.regions, *scene.points)
    for ref in scene.references:
        source = scenes.get(ref.scene)
        if source is None:
            continue
        item = next((x for x in (*source.regions, *source.points) if x.key == ref.entity), None)
        if item is None:
            continue
        target = item.to if ref.to is None else ref.to
        if ref.to is None and target.startswith("/"):
            target = ref.scene + target
        yield replace(item, views=ref.views, to=target,
                      navigation=item.navigation if ref.navigation is None else ref.navigation)


def collect_transitions(scenes: dict) -> list[Transition]:
    """把所有场景的 ``to:`` 声明收成边列表。目标非法的条目跳过。"""
    edges: list[Transition] = []
    for scene_key, scene in scenes.items():
        for item in effective_entities(scenes, scene):
            if not item.is_clickable:
                continue
            target = parse_target(getattr(item, "to", ""), scene_key)
            if target is None:
                continue
            to_scene, to_view = target
            for from_view in (item.views or [""]):
                edges.append(Transition(
                    from_scene=scene_key, from_view=from_view,
                    entity=item.key, to_scene=to_scene, to_view=to_view,
                    navigation=item.navigation))
    for definition_scene, scene in scenes.items():
        for item in (*scene.regions, *scene.points):
            if not item.available_from or not item.is_clickable:
                continue
            target = parse_target(item.to, definition_scene)
            if target is None or target[0] == "@caller":
                continue
            for source_key, source in scenes.items():
                if source.is_subscene or source_key in (definition_scene, target[0]):
                    continue
                if "*" not in item.available_from and source_key not in item.available_from:
                    continue
                for view in ([v.key for v in source.views] or [""]):
                    edges.append(Transition(source_key, view, item.key, *target,
                                            item.navigation, definition_scene))
    return edges


def validate_transitions(scenes: dict) -> list[str]:
    """校验全部 ``to:`` 声明，返回问题列表（空 = 全部合法）。

    只读校验，不修改任何东西——契约是逐步补全的，不完整不该阻断加载。
    """
    problems: list[str] = []
    for scene_key, scene in scenes.items():
        for item in effective_entities(scenes, scene):
            if any(s != "*" and s not in scenes for s in item.available_from):
                problems.append(f"[{scene_key}].[{item.key}] 全局入口作用范围不存在")
            if item.available_from and (not item.is_clickable or not item.to or item.to == "@caller"):
                problems.append(f"[{scene_key}].[{item.key}] 全局入口必须是可点击的固定目标")
            if item.navigation not in ("", "open", "switch", "replace"):
                problems.append(f"[{scene_key}].[{item.key}] 的 navigation 非法")
            if item.navigation and (not item.to or item.to == "@caller"):
                problems.append(f"[{scene_key}].[{item.key}] 无固定目标，不能声明 navigation")
            raw = getattr(item, "to", "")
            if not raw:
                continue
            where = f"[{scene_key}].[{item.key}] 的 to='{raw}'"
            if not item.is_clickable:
                problems.append(f"{where} 属于不可点击实体，不能声明跳转")
                continue
            target = parse_target(raw, scene_key)
            if target is None:
                problems.append(f"{where} 格式非法")
                continue
            to_scene, to_view = target
            if to_scene == "@caller":
                continue
            dest = scenes.get(to_scene)
            if dest is None:
                problems.append(f"{where} 指向不存在的场景 {to_scene}")
                continue
            if to_view and to_view != BASE_VIEW_KEY:
                if not any(v.key == to_view for v in dest.views):
                    problems.append(
                        f"{where} 指向 {to_scene} 中不存在的视图 {to_view}")
            elif to_view == BASE_VIEW_KEY and not dest.views:
                problems.append(
                    f"{where} 指向 {to_scene} 的基底视图，但该场景未开启多视图")
    problems.extend(validate_view_relations(scenes))
    return problems


def find_unreachable_views(scenes: dict) -> list[str]:
    """找出尚未声明入口的非基底、非取景视图。

    基底承接场景入口；取景可通过滚动或翻页到达，不要求点击入口。
    其他视图的缺失仅表示契约待补充，不能据此断言实际页面不可达。
    """
    reached = {(t.to_scene, t.to_view or BASE_VIEW_KEY)
               for t in collect_transitions(scenes)}
    dead: list[str] = []
    for scene_key, scene in scenes.items():
        for view in scene.views:
            if view.key == BASE_VIEW_KEY or view.relation == "viewport":
                continue
            if (scene_key, view.key) not in reached:
                dead.append(f"{scene_key}/{view.key}")
    return sorted(dead)


def entries_of_view(scenes: dict, scene_key: str, view_key: str) -> list[Transition]:
    """哪些按钮点击后进入这个视图。"""
    target = view_key or BASE_VIEW_KEY
    return [t for t in collect_transitions(scenes)
            if t.to_scene == scene_key and (t.to_view or BASE_VIEW_KEY) == target]


def exits_of_view(scenes: dict, scene_key: str, view_key: str) -> list[Transition]:
    """这个视图里的按钮可以转向哪些场景/视图。"""
    target = view_key or BASE_VIEW_KEY
    return [t for t in collect_transitions(scenes)
            if t.from_scene == scene_key and (t.from_view or BASE_VIEW_KEY) == target]


def page_owner(scenes: dict, scene_key: str, view_key: str):
    """解析页面归属；未知引用或循环返回 None。"""
    visited = set()
    node = (scene_key, view_key or BASE_VIEW_KEY)
    while node not in visited:
        visited.add(node)
        scene = scenes.get(node[0])
        if scene is None:
            return None
        view = next((v for v in scene.views if v.key == node[1]), None)
        if view is None:
            return node if not scene.views and node[1] == BASE_VIEW_KEY else None
        if view.relation not in ("tab", "viewport"):
            return node
        owner = view.owner or ("/base" if not view.kind else "")
        target = parse_target(owner, node[0])
        if target is None or target[0] == "@caller":
            return None
        node = (target[0], target[1] or BASE_VIEW_KEY)
    return None


def validate_view_relations(scenes: dict) -> list[str]:
    problems = []
    for key, scene in scenes.items():
        for view in scene.views:
            where = f"{key}/{view.key}"
            if view.relation not in ("page", "tab", "viewport", "modal"):
                problems.append(f"{where} 视图关系非法")
            if view.kind and view.relation in ("tab", "viewport") and not view.owner:
                problems.append(f"{where} 必须声明关联视图 owner")
            visited = {(key, view.key)}
            current_scene, current_view = key, view
            while current_view.owner:
                target = parse_target(current_view.owner, current_scene)
                if target is None:
                    break
                node = (target[0], target[1] or BASE_VIEW_KEY)
                if node in visited:
                    problems.append(f"{where} 结构归属循环")
                    break
                visited.add(node)
                parent = scenes.get(node[0])
                current_view = next((v for v in parent.views if v.key == node[1]), None) if parent else None
                if current_view is None:
                    break
                current_scene = node[0]
            if view.owner:
                target = parse_target(view.owner, key)
                if target is None or page_owner(scenes, *target) is None:
                    problems.append(f"{where} 关联视图不存在或归属循环")
            if page_owner(scenes, key, view.key) is None:
                problems.append(f"{where} 页面归属无法解析（缺失或循环）")
    for edge in collect_transitions(scenes):
        if edge.navigation == "switch" and not edge.is_return:
            source = page_owner(scenes, edge.from_scene, edge.from_view)
            target = page_owner(scenes, edge.to_scene, edge.to_view)
            if source is None or target is None or source != target:
                problems.append(f"{edge.from_scene}.{edge.entity} 切换目标不属于同一页面")
    return problems
