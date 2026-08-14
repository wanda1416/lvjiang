"""基础指令 Mixin：click / drag / wait

报错还是跳过，按失败原因分：
- 脚本 / 布局配错（变量未定义、panel 不在布局里、距离参数非数值、
  未知目标类型）→ 抛错中断，否则后续步骤会在错误的页面上继续乱点
- 运行时状态（panel 尚未对齐、索引越界）→ 记日志后跳过，越界本身
  就是脚本遍历网格时的终止条件
"""

from loguru import logger

from ...core.coord_types import CircleCoordRef, CoordRef, RectCoordRef
from ...i18n import tr
from ..grammar import (
    Click,
    CoordPoint,
    Drag,
    EntityRef,
    Literal,
    PanelGridDrag,
    PanelRef,
    VarRef,
    Wait,
    WaitStable,
)
from ..grammar.ast_nodes import Align
from .signals import WorkflowUserError

# FoundRegion 延迟导入，避免循环依赖
_FoundRegion = None

def _get_found_region_cls():
    global _FoundRegion
    if _FoundRegion is None:
        from ...core.layout_models import FoundRegion
        _FoundRegion = FoundRegion
    return _FoundRegion


class _ActionsMixin:
    """基础指令执行：_exec_click / _exec_drag / _exec_wait

    状态属性与跨 Mixin 方法由 WorkflowEngine 组合后提供。
    """

    def _exec_click(self, node: Click):
        """click scene.coord / scene.panel[row][col] — scene 和 coord 都可以是常量或变量。
        若 target 为 CoordPoint，则按画布归一化坐标反算后点击。
        若 target 为 PanelRef，则查 panel 校准缓存获取格子中心坐标。
        """
        if isinstance(node.target, CoordPoint):
            x, y = self._coord_ratio_to_screen(node.target.rx, node.target.ry)
            self._input.click_screen(x, y, f"coord({node.target.rx},{node.target.ry})")
            return
        if isinstance(node.target, PanelRef):
            x, y = self._panel_ref_to_screen(node.target)
            if x is not None and y is not None:
                self._input.click_screen(x, y, f"panel({node.target.scene}.{node.target.panel}[{node.target.row}][{node.target.col}])")
            return
        if isinstance(node.target, EntityRef):
            # 解析 scene（可能是 str 或 VarRef）
            if isinstance(node.target.scene, VarRef):
                scene = self.variables.get(node.target.scene.name)
                if scene is None:
                    raise WorkflowUserError(
                        f"变量 ${node.target.scene.name} 未定义，无法点击"
                    )
            else:
                scene = node.target.scene

            # 解析 entity（可能是 str 或 VarRef）
            entity = node.target.entity
            if isinstance(entity, VarRef):
                region_val = self.variables.get(entity.name)
                if region_val is None:
                    raise WorkflowUserError(f"变量 ${entity.name} 未定义，无法点击")
                # 检查是否为 find 指令产出的 FoundRegion
                FoundRegionCls = _get_found_region_cls()
                if isinstance(region_val, FoundRegionCls):
                    x, y = self._found_region_to_screen(region_val)
                    self._input.click_screen(x, y, f"find({region_val.text!r})")
                    return
                # 尝试从 coord_meta 查找该 key 对应的 Region
                region_obj = self._find_region_in_coord_meta(region_val)
                if region_obj is not None:
                    x, y = self._ensure_workflow()._region_to_screen(region_obj, jitter=True)
                    self._input.click_screen(x, y, f"{scene}/{region_val}")
                    return
                # 回退：作为 entity key 名查场景配置
                self._ensure_workflow().click_any(str(scene), str(region_val))
            else:
                self._ensure_workflow().click_any(str(scene), entity)
        elif isinstance(node.target, VarRef):
            # 裸变量引用：可能是 CoordRef、find 产出的 FoundRegion
            region_val = self.variables.get(node.target.name)
            if region_val is None:
                raise WorkflowUserError(
                    f"变量 ${node.target.name} 未定义，无法点击"
                )
            # CoordRef 变量：直接点击中心（+ 抖动）
            if isinstance(region_val, CoordRef):
                x, y = self._coord_ref_to_screen(region_val, jitter=True)
                self._input.click_screen(x, y, f"coord_ref({region_val.cx:.3f},{region_val.cy:.3f})")
                return
            FoundRegionCls = _get_found_region_cls()
            if isinstance(region_val, FoundRegionCls):
                x, y = self._found_region_to_screen(region_val)
                self._input.click_screen(x, y, f"find({region_val.text!r})")
                return
            raise WorkflowUserError(
                f"click ${node.target.name}: 变量值不是可点击类型 "
                f"(类型: {type(region_val).__name__})，"
                f"仅支持 CoordRef / find 产出的 FoundRegion"
            )
        else:
            raise WorkflowUserError(f"click: 未知目标类型 {type(node.target).__name__}")

    def _exec_drag(self, node: Drag):
        """drag scene.arrow / scene.panel[row][col] / scene.point1 scene.point2 — 多种拖拽模式。
        若为坐标模式（from_point/to_point），则两端点按画布归一化坐标反算。
        若为点对模式（from_scene_ref/to_scene_ref），则查找两个命名点的屏幕坐标。
        若为 panel 模式，则查校准缓存获取格子中心坐标。
        """
        if isinstance(node.from_point, CoordPoint) and isinstance(node.to_point, CoordPoint):
            x1, y1 = self._coord_ratio_to_screen(node.from_point.rx, node.from_point.ry)
            x2, y2 = self._coord_ratio_to_screen(node.to_point.rx, node.to_point.ry)
            duration = self._resolve_duration(node.duration) if node.duration else None
            self._input.drag_screen(
                x1, y1, x2, y2,
                f"coord({node.from_point.rx},{node.from_point.ry})->({node.to_point.rx},{node.to_point.ry})",
                duration=duration, hold=node.hold,
            )
            return
        if node.from_scene_ref is not None and node.to_scene_ref is not None:
            # 点对模式：drag [scene1].[point1] [scene2].[point2]
            x1, y1 = self._resolve_point_ref_to_screen(node.from_scene_ref, tr("起点"))
            x2, y2 = self._resolve_point_ref_to_screen(node.to_scene_ref, tr("终点"))
            duration = self._resolve_duration(node.duration) if node.duration else None
            self._input.drag_screen(
                x1, y1, x2, y2,
                f"point({node.from_scene_ref.scene}.{node.from_scene_ref.entity})->({node.to_scene_ref.scene}.{node.to_scene_ref.entity})",
                duration=duration, hold=node.hold,
            )
            return
        if isinstance(node.scene, PanelGridDrag):
            # grid 级拖拽：起点为 panel/region 中心，距离按整行/列高度
            grid = node.scene
            # 先尝试作为 panel 查找
            panel_obj = self._find_panel_in_layout(grid.scene, grid.panel)
            if panel_obj is None:
                # 未找到 panel，尝试作为 region 查找
                regions = self._layout.get_scene_regions(grid.scene)
                region_obj = next((r for r in regions if r.key == grid.panel), None)
                if region_obj is None:
                    raise WorkflowUserError(
                        f"drag grid: 布局中未定义 panel/region {grid.scene}.{grid.panel}"
                    )
                # region 中心在截图中的归一化坐标
                cx = region_obj.x_ratio + region_obj.w_ratio / 2
                cy = region_obj.y_ratio + region_obj.h_ratio / 2
                w, h = self._capture.get_capture_size()
                canvas = self._layout.get_canvas()
                x = int((canvas.x_ratio + cx * canvas.w_ratio) * w + self._window_left)
                y = int((canvas.y_ratio + cy * canvas.h_ratio) * h + self._window_top)
                # region 拖拽：使用 region 高度/宽度作为步长
                direction = grid.direction
                distance = grid.distance
                if isinstance(distance, VarRef):
                    distance = self.variables.get(distance.name, 1.0)
                try:
                    distance = float(distance)
                except (TypeError, ValueError):
                    raise WorkflowUserError(f"drag grid: 距离无效: {distance}") from None
                dx, dy = 0, 0
                if direction in ("up", "down"):
                    dy = int(region_obj.h_ratio * canvas.h_ratio * h * distance)
                    if direction == "up":
                        dy = -dy
                    if abs(dy) < 10:
                        dy = 10 if dy >= 0 else -10
                else:
                    dx = int(region_obj.w_ratio * canvas.w_ratio * w * distance)
                    if direction == "left":
                        dx = -dx
                    if abs(dx) < 10:
                        dx = 10 if dx >= 0 else -10
                x2, y2 = x + dx, y + dy
                duration = self._resolve_duration(node.duration) if node.duration else None
                self._input.drag_screen(
                    x, y, x2, y2,
                    f"grid({grid.scene}.{grid.panel}) {direction} {distance}",
                    duration=duration, hold=node.hold,
                )
                logger.debug(f"drag grid: region {grid.scene}.{grid.panel} {direction} {distance}")
                return
            else:
                # panel 中心在截图中的归一化坐标
                cx = panel_obj.x_ratio + panel_obj.w_ratio / 2
                cy = panel_obj.y_ratio + panel_obj.h_ratio / 2
                w, h = self._capture.get_capture_size()
                canvas = self._layout.get_canvas()
                x = int((canvas.x_ratio + cx * canvas.w_ratio) * w + self._window_left)
                y = int((canvas.y_ratio + cy * canvas.h_ratio) * h + self._window_top)
            # 解析 distance（支持 int、float、VarRef）
            distance = grid.distance
            if isinstance(distance, VarRef):
                distance = self.variables.get(distance.name, 1.0)
            try:
                distance = float(distance)  # 支持浮点数（如 0.5 表示半行）
            except (TypeError, ValueError):
                raise WorkflowUserError(f"drag grid: 距离无效: {distance}") from None
            dx, dy = 0, 0
            direction = grid.direction
            # 距离基于 align 实测的 slot + span/2，避免声明尺寸的误差积累
            # slot + span/2 = 从当前 slot 中心到相邻 span 中点的距离
            # 缓存未命中 → 懒加载 align（初始化）
            cache_key = (grid.scene, grid.panel)
            if cache_key not in self._panel_alignments:
                logger.info(f"drag grid: 缓存未命中，懒加载 align: {grid.scene}.{grid.panel}")
                self._exec_align(Align(scene=grid.scene, panel=grid.panel))
            cal = self._panel_alignments.get(cache_key)
            # 无兜底逻辑：align 失败直接报错，不用 panel 高度兜底
            if cal is None or cal.row_slot <= 0:
                logger.error(f"drag grid: align 失败，无法计算拖拽距离: {grid.scene}.{grid.panel}")
                return
            if direction in ("up", "down"):
                step_norm = cal.row_slot + cal.row_span / 2.0
                panel_pixel_h = panel_obj.h_ratio * canvas.h_ratio * h
                dy = int(step_norm * panel_pixel_h * distance)
                if direction == "up":
                    dy = -dy
                if abs(dy) < 10:
                    dy = 10 if dy >= 0 else -10
            else:
                if cal.col_slot <= 0:
                    logger.error(f"drag grid: align 列数据无效: {grid.scene}.{grid.panel}")
                    return
                step_norm = cal.col_slot + cal.col_span / 2.0
                panel_pixel_w = panel_obj.w_ratio * canvas.w_ratio * w
                dx = int(step_norm * panel_pixel_w * distance)
                if direction == "left":
                    dx = -dx
                if abs(dx) < 10:
                    dx = 10 if dx >= 0 else -10
            x2, y2 = x + dx, y + dy
            duration = self._resolve_duration(node.duration) if node.duration else None
            self._input.drag_screen(
                x, y, x2, y2,
                f"grid({grid.scene}.{grid.panel}) {direction} {distance}",
                duration=duration, hold=node.hold,
            )
            # drag 后界面已滚动，失效对齐缓存（不立即刷新，避免截到滚动动画残影）
            # 下次访问时懒加载重新对齐（此时滚动动画已完成）
            self._panel_alignments.pop(cache_key, None)
            logger.debug(f"drag grid: 已失效对齐缓存: {grid.scene}.{grid.panel}")
            return
        if isinstance(node.scene, PanelRef):
            x, y = self._panel_ref_to_screen(node.scene)
            if x is None or y is None:
                return
            # scene / panel 支持静态字符串或 $var（与 EntityRef 动态引用语义一致）
            scene_key = self._resolve(node.scene.scene) if isinstance(node.scene.scene, VarRef) else node.scene.scene
            panel_key = self._resolve(node.scene.panel) if isinstance(node.scene.panel, VarRef) else node.scene.panel
            # panel drag：根据方向和距离计算拖拽终点
            # up = 手指向上划 = 内容下移 = 显示上方内容
            # down = 手指向下划 = 内容上移 = 显示下方内容
            # left = 手指向左划 = 内容右移 = 显示左侧内容
            # right = 手指向右划 = 内容左移 = 显示右侧内容
            panel_obj = self._find_panel_in_layout(scene_key, panel_key)
            if panel_obj is None:
                raise WorkflowUserError(
                    f"drag panel: 布局中未定义 panel "
                    f"{scene_key}.{panel_key}"
                )
            # 解析 distance（支持 int、float、VarRef）
            distance = node.distance
            if isinstance(distance, VarRef):
                distance = self.variables.get(distance.name, 1.0)
            try:
                distance = float(distance)  # 支持浮点数（如 0.5 表示半行）
            except (TypeError, ValueError):
                raise WorkflowUserError(f"drag: 距离无效: {distance}") from None
            w, h = self._capture.get_capture_size()
            canvas = self._layout.get_canvas()
            direction = node.direction or "down"
            # 距离基于 align 实测的 slot + span/2（与 grid drag 一致）
            cache_key = (scene_key, panel_key)
            cal = self._panel_alignments.get(cache_key)
            # _panel_ref_to_screen 已触发懒加载，此处 cal 应有效
            if cal is None:
                logger.error(f"drag panel: align 失败: {scene_key}.{panel_key}")
                return
            dx, dy = 0, 0
            if direction in ("up", "down"):
                # 垂直拖拽：按实测行周期计算
                step_norm = cal.row_slot + cal.row_span / 2.0
                panel_pixel_h = panel_obj.h_ratio * canvas.h_ratio * h
                dy = int(step_norm * panel_pixel_h * distance)
                if direction == "up":
                    dy = -dy  # 手指向上划
                if abs(dy) < 10:
                    dy = 10 if dy >= 0 else -10
            else:
                # 水平拖拽：按实测列周期计算
                step_norm = cal.col_slot + cal.col_span / 2.0
                panel_pixel_w = panel_obj.w_ratio * canvas.w_ratio * w
                dx = int(step_norm * panel_pixel_w * distance)
                if direction == "left":
                    dx = -dx  # 手指向左划
                if abs(dx) < 10:
                    dx = 10 if dx >= 0 else -10
            x2, y2 = x + dx, y + dy
            duration = self._resolve_duration(node.duration) if node.duration else None
            self._input.drag_screen(
                x, y, x2, y2,
                f"panel({node.scene.scene}.{node.scene.panel}[{node.scene.row}][{node.scene.col}]) {direction} {distance}",
                duration=duration, hold=node.hold,
            )
            # drag 后界面已滚动，失效对齐缓存（不立即刷新，避免截到滚动动画残影）
            # 下次访问时懒加载重新对齐（此时滚动动画已完成）
            self._panel_alignments.pop((node.scene.scene, node.scene.panel), None)
            logger.debug(f"drag panel: 已失效对齐缓存: {node.scene.scene}.{node.scene.panel}")
            return
        if isinstance(node.scene, EntityRef):
            # 解析 scene（可能是 str 或 VarRef）
            if isinstance(node.scene.scene, VarRef):
                scene = self.variables.get(node.scene.scene.name)
                if scene is None:
                    raise WorkflowUserError(
                        f"变量 ${node.scene.scene.name} 未定义，无法拖拽"
                    )
            else:
                scene = node.scene.scene

            # 解析 key（可能是 str 或 VarRef）
            key = node.scene.entity
            if isinstance(key, VarRef):
                key_val = self.variables.get(key.name)
                if key_val is None:
                    raise WorkflowUserError(f"变量 ${key.name} 未定义，无法拖拽")
                key = key_val

            duration = self._resolve_duration(node.duration) if node.duration else None
            hold = node.hold

            # 先尝试作为 arrow 查找
            arrows = self._layout.get_scene_arrows(str(scene))
            arrow = next((a for a in arrows if a.key == str(key)), None)
            if arrow is not None:
                self._ensure_workflow().drag_arrow(str(scene), str(key), duration=duration, hold=hold)
                return

            # 未找到 arrow，尝试作为 region 查找
            regions = self._layout.get_scene_regions(str(scene))
            region = next((r for r in regions if r.key == str(key)), None)
            if region is not None:
                # region 中心作为起点，利用 w/h 计算终点
                cx = region.x_ratio + region.w_ratio / 2
                cy = region.y_ratio + region.h_ratio / 2
                w, h = self._capture.get_capture_size()
                canvas = self._layout.get_canvas()
                x = int((canvas.x_ratio + cx * canvas.w_ratio) * w + self._window_left)
                y = int((canvas.y_ratio + cy * canvas.h_ratio) * h + self._window_top)
                # 默认向上拖拽一个 region 高度
                dy = int(region.h_ratio * canvas.h_ratio * h)
                if abs(dy) < 10:
                    dy = 10
                x2 = x
                y2 = y - dy
                self._input.drag_screen(
                    x, y, x2, y2,
                    f"region({scene}.{key}) up",
                    duration=duration, hold=hold,
                )
                logger.debug(f"drag region: {scene}.{key} up (default)")
                return

            # 尝试作为 point 查找 → 不允许单独 drag
            points = self._layout.get_scene_points(str(scene))
            point = next((p for p in points if p.key == str(key)), None)
            if point is not None:
                raise WorkflowUserError(
                    f"drag [{scene}].[{key}]: Point 无法单独 drag（无 w/h 计算终点），"
                    f"请使用两点模式 drag [{scene}].{key} [{scene}].<另一个点>"
                )

            raise WorkflowUserError(
                f"drag: 场景 [{scene}] 的 arrow/region/point 未绑定: {key}，"
                f"请在场景布局编辑器中绑定后重试"
            )
        else:
            raise WorkflowUserError(f"drag: 未知目标类型 {type(node.scene).__name__}")

    def _exec_wait(self, node: Wait):
        delay = node.delay
        if isinstance(delay, tuple) and len(delay) == 2:
            # 随机范围等待：wait (min, max)
            import random
            lo, hi = float(delay[0]), float(delay[1])
            seconds = random.uniform(lo, hi)
            logger.debug(f"随机等待 {lo}~{hi}s → {seconds:.2f}s")
            self._ensure_workflow().wait_seconds(seconds)
        elif isinstance(delay, VarRef):
            # 动态等待：wait $var → 解析变量值
            val = self.variables.get(delay.name)
            if isinstance(val, tuple) and len(val) == 2:
                # 随机范围等待：wait $var 其中 $var = (min, max)
                import random
                lo, hi = float(val[0]), float(val[1])
                seconds = random.uniform(lo, hi)
                logger.debug(f"动态随机等待 ${delay.name} = ({lo}, {hi}) → {seconds:.2f}s")
                self._ensure_workflow().wait_seconds(seconds)
            elif isinstance(val, (int, float)):
                self._ensure_workflow().wait_seconds(float(val))
                logger.debug(f"动态等待 ${delay.name} = {val}s")
            else:
                raise WorkflowUserError(
                    f"wait ${delay.name} 不是数值或范围类型: {val}"
                )
        elif isinstance(delay, Literal):
            val = delay.value
            if isinstance(val, (int, float)):
                self._ensure_workflow().wait_seconds(float(val))
            else:
                # 命名延迟
                self._ensure_workflow().wait_delay(str(val))
        else:
            self._ensure_workflow().wait_delay(str(delay))

    def _exec_wait_stable(self, node: WaitStable):
        """等待画面稳定：连续截图对比，差异低于阈值持续 stable_duration 秒后继续；
        超时仅记警告不中断流程

        参数支持三种形式：float / Literal(@delay / 数值) / VarRef($var)
        支持 on [scene].[region] 区域限定，只对指定区域做 diff 对比。
        """
        timeout = self._resolve_ws_param(node.timeout, "timeout")
        threshold = self._resolve_ws_param(node.threshold, "threshold")
        interval = self._resolve_ws_param(node.interval, "interval")
        stable_duration = self._resolve_ws_param(node.stable_duration, "duration")
        least = self._resolve_ws_param(node.least, "least")
        crop_box = self._resolve_area_crop(node.area)
        self._ensure_workflow().wait_stable(
            timeout=timeout,
            threshold=threshold,
            interval=interval,
            stable_duration=stable_duration,
            least=least,
            crop_box=crop_box,
        )

    def _resolve_ws_param(self, param, name: str) -> float:
        """解析 wait stable 参数：float / Literal / VarRef → float

        - float: 直接返回
        - Literal(数值): 直接返回
        - Literal(字符串): 命名延迟，取范围中值
        - VarRef: 从变量表查找，必须为数值
        """
        if isinstance(param, (int, float)):
            return float(param)
        if isinstance(param, Literal):
            val = param.value
            if isinstance(val, (int, float)):
                return float(val)
            # 命名延迟：取范围中值
            delay_param = self._delay_params.get(str(val))
            if delay_param is None:
                raise WorkflowUserError(
                    f"wait stable {name}: 等待参数 @{val} 未定义"
                )
            lo, hi = delay_param.range
            return (lo + hi) / 2.0
        if isinstance(param, VarRef):
            val = self.variables.get(param.name)
            if val is None:
                raise WorkflowUserError(
                    f"wait stable {name}: 变量 ${param.name} 未定义"
                )
            if not isinstance(val, (int, float)):
                raise WorkflowUserError(
                    f"wait stable {name}: ${param.name} 不是数值类型: {val}"
                )
            return float(val)
        # fallback: 尝试直接转换
        return float(param)

    def _resolve_area_crop(self, area) -> dict | None:
        """将 area (EntityRef) 解析为像素裁剪框 {'x', 'y', 'w', 'h'}

        用于 wait stable on [scene].[entity] 的区域限定。
        返回 None 表示全画面检测（area 为 None 时）。
        scene 和 entity 均支持 $var 动态引用。
        """
        if area is None:
            return None
        if isinstance(area, EntityRef):
            # 解析 scene（支持 VarRef）
            if isinstance(area.scene, VarRef):
                scene = self.variables.get(area.scene.name)
                if scene is None:
                    raise WorkflowUserError(
                        f"wait stable on: 变量 ${area.scene.name} 未定义"
                    )
                scene = str(scene)
            else:
                scene = str(area.scene)

            # 解析 entity（支持 VarRef）
            entity_key = area.entity
            if isinstance(entity_key, VarRef):
                entity_key = self.variables.get(entity_key.name)
                if entity_key is None:
                    raise WorkflowUserError(
                        f"wait stable on: 变量 ${area.entity.name} 未定义"
                    )
            entity_key = str(entity_key)

            regions = self._layout.get_scene_regions(scene)
            region_obj = next((r for r in regions if r.key == entity_key), None)
            if region_obj is None:
                raise WorkflowUserError(
                    f"wait stable on [{scene}].[{entity_key}]: 区域未绑定"
                )
            x_r, y_r, w_r, h_r = (
                region_obj.x_ratio, region_obj.y_ratio,
                region_obj.w_ratio, region_obj.h_ratio,
            )
            # 归一化坐标 → 像素（相对于截图，不含窗口偏移）
            w, h = self._capture.get_capture_size()
            canvas = self._layout.get_canvas()
            px = int((canvas.x_ratio + x_r * canvas.w_ratio) * w)
            py = int((canvas.y_ratio + y_r * canvas.h_ratio) * h)
            pw = max(1, int(w_r * canvas.w_ratio * w))
            ph = max(1, int(h_r * canvas.h_ratio * h))
            return {"x": px, "y": py, "w": pw, "h": ph}
        raise WorkflowUserError(f"wait stable on: 不支持的区域类型: {type(area)}")

    def _found_region_to_screen(self, found_region, jitter: bool = True) -> tuple[int, int]:
        """FoundRegion → 屏幕坐标（取区域中心，可选抖动）"""
        import random
        w, h = self._capture.get_capture_size()
        canvas = self._layout.get_canvas()

        canvas_x = canvas.x_ratio * w
        canvas_y = canvas.y_ratio * h
        canvas_w = canvas.w_ratio * w
        canvas_h = canvas.h_ratio * h

        cx = canvas_x + (found_region.x_ratio + found_region.w_ratio / 2) * canvas_w
        cy = canvas_y + (found_region.y_ratio + found_region.h_ratio / 2) * canvas_h

        if jitter:
            jitter_ratio = self._input_sim.region_jitter_ratio
            region_w = found_region.w_ratio * canvas_w
            region_h = found_region.h_ratio * canvas_h
            cx += region_w * random.uniform(-jitter_ratio, jitter_ratio)
            cy += region_h * random.uniform(-jitter_ratio, jitter_ratio)

        return int(self._window_left + cx), int(self._window_top + cy)

    def _resolve_point_ref_to_screen(self, scene_ref: EntityRef, label: str = "") -> tuple[int, int]:
        """EntityRef(scene=场景名, entity=点名) → 屏幕坐标

        用于 drag 点对模式：查找布局中定义的 Point 并转换为屏幕坐标。
        """
        # 解析场景名（支持 VarRef）
        if isinstance(scene_ref.scene, VarRef):
            scene = self.variables.get(scene_ref.scene.name)
            if scene is None:
                raise WorkflowUserError(
                    f"drag {label}: 变量 ${scene_ref.scene.name} 未定义"
                )
        else:
            scene = str(scene_ref.scene)

        # 解析点名（支持 VarRef）
        if isinstance(scene_ref.entity, VarRef):
            point_key = self.variables.get(scene_ref.entity.name)
            if point_key is None:
                raise WorkflowUserError(
                    f"drag {label}: 变量 ${scene_ref.entity.name} 未定义"
                )
        else:
            point_key = str(scene_ref.entity)

        # 从布局中查找 Point
        points = self._layout.get_scene_points(str(scene))
        point = next((p for p in points if p.key == str(point_key)), None)
        if point is None:
            raise WorkflowUserError(
                f"drag {label}: 场景 [{scene}] 的坐标点未绑定: {point_key}"
            )

        # Point → 屏幕坐标（带半径内随机偏移）
        return self._ensure_workflow()._point_to_screen(point)

    def _coord_ref_to_screen(self, coord_ref: CoordRef, jitter: bool = True) -> tuple[int, int]:
        """CoordRef → 屏幕绝对坐标（可选抖动）

        RectCoordRef: 在 w/h 范围内抖动
        CircleCoordRef: 在 r 范围内抖动
        CoordRef (基类): 仅像素级抖动
        """
        import random
        w, h = self._capture.get_capture_size()
        canvas = self._layout.get_canvas()

        canvas_x = canvas.x_ratio * w
        canvas_y = canvas.y_ratio * h
        canvas_w = canvas.w_ratio * w
        canvas_h = canvas.h_ratio * h

        cx = canvas_x + coord_ref.cx * canvas_w
        cy = canvas_y + coord_ref.cy * canvas_h

        if jitter:
            jitter_ratio = self._input_sim.region_jitter_ratio
            if isinstance(coord_ref, RectCoordRef) and coord_ref.w > 0 and coord_ref.h > 0:
                # RectCoordRef: 在 w/h 范围内抖动
                region_w = coord_ref.w * canvas_w
                region_h = coord_ref.h * canvas_h
                cx += region_w * random.uniform(-jitter_ratio, jitter_ratio)
                cy += region_h * random.uniform(-jitter_ratio, jitter_ratio)
            elif isinstance(coord_ref, CircleCoordRef) and coord_ref.r > 0:
                # CircleCoordRef: 在 r 范围内抖动
                region_r = coord_ref.r * min(canvas_w, canvas_h)
                cx += region_r * random.uniform(-jitter_ratio, jitter_ratio)
                cy += region_r * random.uniform(-jitter_ratio, jitter_ratio)
            else:
                # 基类 CoordRef: 仅像素级随机偏移
                pixel_jitter = self._input_sim.click_random_offset
                cx += random.uniform(-pixel_jitter, pixel_jitter)
                cy += random.uniform(-pixel_jitter, pixel_jitter)

        return int(self._window_left + cx), int(self._window_top + cy)
