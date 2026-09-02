"""页面切换契约：area 声明"点了到哪去"，据此校验视图的入口与转移。

声明放在**场景定义**（游戏语义，不随布局变），可用性放在**布局**
（``Region.disabled`` 表达"这个布局上没有这条边"）。这个分工让
"桌面端按 B 一步到背包、安卓端要 主页→菜单→背包"这类拓扑差异不必污染语义层。

契约只做声明和校验，**不驱动执行**：现有 wf 与 Python 编排一行都不用改，
契约先行、逐步补全，后续读代码的人（和 AI）能直接看懂转移逻辑。
"""
from __future__ import annotations

from dataclasses import dataclass

from .scene_definition_models import BASE_VIEW_KEY


@dataclass(frozen=True)
class Transition:
    """一条页面切换边。"""

    from_scene: str
    from_view: str          # 触发按钮所在视图（"" = 基底）
    entity: str             # 触发按钮 key
    to_scene: str           # 目标场景
    to_view: str            # 目标视图（"" = 基底）

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
    scene, _, view = text.partition("/")
    scene = scene.strip() or current_scene
    view = view.strip()
    if not scene:
        return None
    return scene, view


def collect_transitions(scenes: dict) -> list[Transition]:
    """把所有场景的 ``to:`` 声明收成边列表。目标非法的条目跳过。"""
    edges: list[Transition] = []
    for scene_key, scene in scenes.items():
        for item in (*scene.regions, *scene.points):
            if not item.is_clickable:
                continue
            target = parse_target(getattr(item, "to", ""), scene_key)
            if target is None:
                continue
            to_scene, to_view = target
            for from_view in (item.views or [""]):
                edges.append(Transition(
                    from_scene=scene_key, from_view=from_view,
                    entity=item.key, to_scene=to_scene, to_view=to_view))
    return edges


def validate_transitions(scenes: dict) -> list[str]:
    """校验全部 ``to:`` 声明，返回问题列表（空 = 全部合法）。

    只读校验，不修改任何东西——契约是逐步补全的，不完整不该阻断加载。
    """
    problems: list[str] = []
    for scene_key, scene in scenes.items():
        for item in (*scene.regions, *scene.points):
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
    return problems


def find_unreachable_views(scenes: dict) -> list[str]:
    """找出没有任何入口的非基底、非同态视图。

    两类视图**天然没有入口**，不算死视图：

    - **基底视图**：场景入口，不需要场景内的按钮指向它；
    - **同态视图**：与基底同一层，只是滚动/翻页后的另一个取景（菜单的
      page_1 / page_2）。没有任何按钮"进入"它，你只是把同一页滚过去了。

    其余视图若没有任何 ``to:`` 指过来，就是**死视图**——要么漏声明了入口，
    要么它根本不该是个独立视图（也许该标成同态）。
    """
    reached = {(t.to_scene, t.to_view or BASE_VIEW_KEY)
               for t in collect_transitions(scenes)}
    dead: list[str] = []
    for scene_key, scene in scenes.items():
        for view in scene.views:
            if view.key == BASE_VIEW_KEY or getattr(view, "homomorphic", False):
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
