"""timeline 块的执行：把条目编译成后端原语，再整块下发

编译在**下发之前**全部完成：坐标、按键、时长都先解析好，任何一条不合法就整块
不执行。这不是洁癖——a11y 多 stroke 手势任一路失败是整组取消，半截状态会留下
"摇杆推住了、点击没落地而手指还按着"，比不执行糟得多。

跨端差异用条目级的 `env:` 守卫表达，不必把整块复制两份：

    @0.0  env:"desktop" -> press "S" hold 2.4
    @0.0  env:"android" -> drag [general_move].[move_backward] hold 2.4
    @0.4  click [general_combat].[tiaoyue]

守卫是编译期选择（run_env 在块开始前已定），所以它不威胁时序；`if` 仍然禁止，
因为它的条件可以调 OCR。移动之所以非分不可：桌面按 WASD、设备推摇杆，不是"同一
动作换绑定"——arrow 没有 activation_key，`drag` 在桌面不会转成按键。
"""

from __future__ import annotations

from loguru import logger

from ...core.input_base import TimelineUnsupported
from ...core.timeline import TimelineStep, describe_timeline
from ..grammar import (
    Click,
    Drag,
    EntityRef,
    Press,
    Timeline,
    TimelineEntry,
    VarRef,
)
from ..grammar.ast_nodes import PressMode
from .signals import WorkflowUserError


class _TimelineMixin:
    """timeline 块的编译与执行"""

    def _exec_timeline(self, node: Timeline) -> None:
        # 环境守卫在编译期过滤：run_env 在块开始前就定了，所以它不消耗时间预算，
        # 这也是守卫能进块、而 `if`（条件可以调 OCR）不能的分界。
        entries = [e for e in node.entries
                   if not e.env or e.env == self.run_env]
        steps = [self._compile_timeline_entry(entry) for entry in entries]
        logger.info(f"[时间线] {describe_timeline(steps)}")
        try:
            self._input.run_timeline(steps)
        except TimelineUnsupported as exc:
            raise WorkflowUserError(
                f"第 {node.line_no} 行：{exc}。"
                "把差异写成两个 timeline 块放在 is_device() 分支下，"
                "或改用当前通道支持的写法"
            ) from exc

    # ─── 条目编译 ────────────────────────────────────────

    def _compile_timeline_entry(self, entry: TimelineEntry) -> TimelineStep:
        offset = self._resolve_hold_duration(
            entry.offset, "timeline", term="@偏移", noun="@偏移 ",
            allow_zero=True)
        action = entry.action
        if isinstance(action, Press):
            return self._compile_timeline_press(entry, offset, action)
        if isinstance(action, Click):
            return self._compile_timeline_click(entry, offset, action)
        if isinstance(action, Drag):
            return self._compile_timeline_drag(entry, offset, action)
        raise WorkflowUserError(
            f"第 {entry.line_no} 行：timeline 不支持 "
            f"{type(action).__name__} 类型的语句"
        )

    def _compile_timeline_press(
        self, entry: TimelineEntry, offset: float, node: Press,
    ) -> TimelineStep:
        if node.mode in (PressMode.DOWN, PressMode.UP):
            # 块内的 down/up 是自相矛盾的写法：时间线本来就用偏移 + hold 表达
            # "从几点按到几点"，再来一对裸 down/up 只会让释放时机没人管。
            raise WorkflowUserError(
                f"第 {entry.line_no} 行：timeline 内不能用 press ... "
                f"{node.mode.value}，按住时长请写 hold"
            )
        # 组合键一路一键：每一路本来就各带自己的偏移，写成多条更直白，
        # 也省掉"一路步骤里塞多个键"这种既要排程又要同步的中间形态
        chain = node.keys or (node.key,)
        if len(chain) > 1:
            raise WorkflowUserError(
                f"第 {entry.line_no} 行：timeline 内的 press 不支持组合键，"
                "请每个键各写一条 @偏移"
            )
        key = self._resolve(chain[0]) if isinstance(chain[0], VarRef) else chain[0]
        if key is None:
            raise WorkflowUserError(
                f"第 {entry.line_no} 行：press 的按键变量未定义")
        hold = (
            self._resolve_hold_duration(node.duration, "press")
            if node.mode is PressMode.HOLD else 0.0
        )
        return TimelineStep(
            offset=offset, kind="key", key=str(key), hold=hold,
            label=f'press "{key}"')

    def _compile_timeline_click(
        self, entry: TimelineEntry, offset: float, node: Click,
    ) -> TimelineStep:
        if not isinstance(node.target, EntityRef):
            raise WorkflowUserError(
                f"第 {entry.line_no} 行：timeline 里的 click 只支持 "
                "[场景].[实体]，这样每端才能按 activation_key 各自选按键或触点"
            )
        scene, key = self._timeline_entity_names(entry, node.target)
        hold = (
            self._resolve_hold_duration(node.hold, "click")
            if node.hold is not None else 0.0
        )
        label = f"{scene}/{key}"
        activation = self._timeline_activation_key(scene, key)
        if activation:
            return TimelineStep(
                offset=offset, kind="key", key=activation, hold=hold,
                label=label)
        x, y = self._timeline_entity_center(entry, scene, key)
        return TimelineStep(
            offset=offset, kind="touch", x1=x, y1=y, x2=x, y2=y,
            hold=hold, label=label)

    def _compile_timeline_drag(
        self, entry: TimelineEntry, offset: float, node: Drag,
    ) -> TimelineStep:
        if not isinstance(node.scene, EntityRef):
            raise WorkflowUserError(
                f"第 {entry.line_no} 行：timeline 里的 drag 只支持 "
                "[场景].[方向]（arrow），面板与网格拖拽不是并发输入"
            )
        scene, key = self._timeline_entity_names(entry, node.scene)
        arrows = self._layout.get_scene_arrows(scene)
        arrow = next((a for a in arrows if a.key == key), None)
        if arrow is None:
            raise WorkflowUserError(
                f"第 {entry.line_no} 行：timeline 的 drag 需要 arrow，"
                f"[{scene}].[{key}] 不是；两点形态请各写一条 @偏移"
            )
        workflow = self._ensure_workflow()
        points = {p.key: p for p in self._layout.get_scene_points(scene)}
        start = points.get(arrow.from_key)
        end = points.get(arrow.to_key) if arrow.to_key else None
        if start is None or (arrow.to_key and end is None):
            raise WorkflowUserError(
                f"第 {entry.line_no} 行：方向 [{scene}].[{key}] 的端点坐标未绑定"
            )
        x1, y1 = workflow._point_to_screen(start, jitter=False)
        if end is not None:
            x2, y2 = workflow._point_to_screen(end, jitter=False)
        else:
            x2, y2 = workflow._ratio_to_screen(
                arrow.to_cx_ratio, arrow.to_cy_ratio)
        move = self._resolve_hold_duration(
            node.duration, "drag duration") if node.duration is not None else 0.1
        hold = float(node.hold) if node.hold is not None else 0.0
        return TimelineStep(
            offset=offset, kind="touch", x1=x1, y1=y1, x2=x2, y2=y2,
            move=move, hold=hold, label=f"{scene}/{key}")

    # ─── 实体解析 ────────────────────────────────────────

    def _timeline_entity_names(
        self, entry: TimelineEntry, ref: EntityRef,
    ) -> tuple[str, str]:
        scene = ref.scene
        if isinstance(scene, VarRef):
            scene = self.variables.get(scene.name)
            if scene is None:
                raise WorkflowUserError(
                    f"第 {entry.line_no} 行：变量 ${ref.scene.name} 未定义")
        entity = ref.entity
        if isinstance(entity, VarRef):
            entity = self.variables.get(entity.name)
            if entity is None:
                raise WorkflowUserError(
                    f"第 {entry.line_no} 行：变量 ${ref.entity.name} 未定义")
        return str(scene), str(entity)

    def _timeline_activation_key(self, scene: str, key: str) -> str:
        """实体绑定的激活按键；没绑定返回空串（走触点）"""
        for items in (self._layout.get_scene_regions(scene),
                      self._layout.get_scene_points(scene)):
            item = next((i for i in items if i.key == key), None)
            if item is not None:
                return str(getattr(item, "activation_key", "") or "")
        return ""

    def _timeline_entity_center(
        self, entry: TimelineEntry, scene: str, key: str,
    ) -> tuple[int, int]:
        """实体中心的屏幕坐标；不抖动——时间线要的是可复现的落点"""
        workflow = self._ensure_workflow()
        region = next(
            (r for r in self._layout.get_scene_regions(scene) if r.key == key),
            None)
        if region is not None:
            return workflow._region_to_screen(region, jitter=False)
        point = next(
            (p for p in self._layout.get_scene_points(scene) if p.key == key),
            None)
        if point is not None:
            return workflow._point_to_screen(point, jitter=False)
        raise WorkflowUserError(
            f"第 {entry.line_no} 行：[{scene}].[{key}] 未在当前布局绑定坐标，"
            "请在场景管理里绑定后重试"
        )
