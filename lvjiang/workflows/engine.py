"""工作流 DSL v2 引擎

运行时主体：直接持有硬件后端，管理 session/context 生命周期，
递归执行 AST 节点，支持完整控制流（if/for/loop/break/goto）。
子工作流通过创建独立 engine 实例实现天然隔离，无需 save/restore。
"""

import traceback
from typing import Any, Callable

import numpy as np
from loguru import logger
from pathlib import Path

from ..config import DelayConfig
from ..core.capture_base import CaptureBackend
from ..core.ocr import OCREngine
from ..core.input_base import InputBackend
from ..core.scene_registry import Layout

from .grammar import (
    parse_file,
    Program,
    Click, Drag, Wait, Scan, Recognize, Collect, Log, Call,
    If, For, ForRange, Loop, Break, Return, Label, Goto, Eval, EvalFieldChainAssign, FuncCall,
    SceneRef, PanelRef, PanelGridDrag, VarRef, KeywordRef, Literal, FieldAccess, CoordPoint, ByClause,
    Contains, Equals, InList, IsEmpty,
    GreaterThan, LessThan, GreaterEqual, LessEqual, NotEqual, NumericEqual,
    Not, And, Or,
)
from .grammar.ast_nodes import Align
from .base import BaseWorkflow
from .align import detect_grid, GridAlignment


# ─── 用户可见的 DSL 错误 ──────────────────────────────────

class WorkflowUserError(Exception):
    """DSL 脚本中用户操作引发的可预期错误（类型不匹配、字段不存在等）"""


# ─── 控制流信号 ───────────────────────────────────────────

class _BreakSignal(Exception):
    """break 语句触发的跳出信号"""


class _ReturnSignal(Exception):
    """return 语句触发的正常退出信号"""


class _GotoSignal(Exception):
    """goto 语句触发的跳转信号"""

    def __init__(self, target: str):
        self.target = target


# ─── 引擎 ─────────────────────────────────────────────────

class WorkflowEngine:
    """DSL v2 工作流运行时

    直接持有硬件后端，管理 session/context 生命周期。
    通过 _ensure_workflow() 懒创建 BaseWorkflow 作为游戏操作委托。
    """

    def __init__(
        self,
        *,
        capture: CaptureBackend,
        ocr: OCREngine,
        input_ctrl: InputBackend,
        layout: Layout,
        delay_config: DelayConfig | None = None,
        window_left: int = 0,
        window_top: int = 0,
        stop_check: Callable[[], bool] | None = None,
    ):
        # 硬件后端（直接持有）
        self._capture = capture
        self._ocr = ocr
        self._input = input_ctrl
        self._layout = layout
        self._delay = delay_config or DelayConfig()
        self._window_left = window_left
        self._window_top = window_top
        self._stop_check = stop_check or (lambda: False)
        # 执行状态
        self.variables: dict = {}
        self.output: dict = {}
        self._coord_meta: dict[str, dict] = {}
        self._base_dir: Path | None = None
        # panel 对齐缓存：(scene_key, panel_key) → GridAlignment
        self._panel_alignments: dict[tuple[str, str], GridAlignment] = {}
        # session / context（公开属性，UI 层注入）
        self.session: dict = {}          # 持久状态（UI 层从 SessionManager 加载）
        self.context: dict = {}          # 运行时上下文（每次执行自动初始化空 dict）
        self._save_callback: Callable | None = None
        # 游戏操作委托（execute 时懒创建）
        self._workflow: BaseWorkflow | None = None

    @property
    def _session(self) -> dict:
        """兼容旧访问方式，DSL 内部使用"""
        return self.session

    @_session.setter
    def _session(self, value: dict | None):
        self.session = value if value is not None else {}

    @property
    def _context(self) -> dict:
        """兼容旧访问方式，DSL 内部使用"""
        return self.context

    @_context.setter
    def _context(self, value: dict | None):
        self.context = value if value is not None else {}

    def _ensure_workflow(self) -> BaseWorkflow:
        """懒创建 BaseWorkflow 作为游戏操作委托"""
        if self._workflow is None:
            self._workflow = BaseWorkflow(
                capture=self._capture,
                ocr=self._ocr,
                input_ctrl=self._input,
                layout=self._layout,
                delay_config=self._delay,
                window_left=self._window_left,
                window_top=self._window_top,
                stop_check=self._stop_check,
            )
        return self._workflow

    def execute(self, source, *, initial_variables: dict | None = None,
                _reset_context: bool = True) -> dict:
        """统一执行入口

        Args:
            source: .wf 文件路径 或 BaseWorkflow 实例
            initial_variables: 外部注入的初始变量
            _reset_context: 是否重置 context（仅顶层调用为 True）

        Returns:
            dict: collect 累积结果
        """
        if initial_variables:
            self.variables.update(initial_variables)

        # context 是临时状态，仅顶层执行时重置
        # 子工作流通过 _exec_call 共享父 context，不能重置
        if _reset_context:
            self.context = {}

        # Python 工作流实例
        if isinstance(source, BaseWorkflow):
            return self._execute_python_workflow(source)

        # DSL .wf 文件
        source_path = Path(source)
        if source_path.suffix == ".wf":
            return self._execute_dsl(source_path)
        raise ValueError(f"不支持的执行源: {source}")

    def _execute_dsl(self, wf_path: Path) -> dict:
        """加载并执行 .wf 文件"""
        self._ensure_workflow()
        resolved = Path(wf_path).resolve()
        self._base_dir = resolved.parent
        program = parse_file(resolved)
        logger.info(f"=== DSL 工作流开始: {resolved.stem} ({len(program.body)} 条顶层指令) ===")

        try:
            self._exec_body(program.body)
        except _GotoSignal as sig:
            logger.error(f"goto 目标标签不存在: {sig.target}")
            return self.output
        except _ReturnSignal:
            logger.info(f"=== DSL 工作流正常返回，收集到 {len(self.output)} 项数据 ===")
            return self.output

        logger.info(f"=== DSL 工作流完成，收集到 {len(self.output)} 项数据 ===")
        return self.output

    def _execute_python_workflow(self, workflow: BaseWorkflow) -> dict:
        """执行 Python 工作流实例"""
        workflow.reset_state()
        workflow.variables.update(self.variables)
        logger.info(f"=== Python 工作流开始: {workflow.__class__.__name__} ===")
        try:
            result = workflow.run()
        except Exception as e:
            logger.error(f"Python 工作流异常: {e}\n{traceback.format_exc()}")
            result = workflow.output
        logger.info(f"=== Python 工作流完成，收集到 {len(result)} 项数据 ===")
        return result

    # ─── 语句执行 ──────────────────────────────────────────

    def _exec_body(self, stmts: list) -> None:
        """执行一组语句（含 goto 跳转支持）

        collect 的输出记录到 self._collect_output，不中断执行流。
        仅 break/goto/停止请求 才会提前终止。
        goto 信号会向上传播，直到找到包含目标标签的层级。
        """
        pc = 0
        while pc < len(stmts):
            if self._stop_check():
                logger.info("工作流被用户停止")
                return

            node = stmts[pc]

            try:
                self._exec_stmt(node)
            except _GotoSignal as sig:
                target = sig.target
                # 检查当前层级的标签索引
                local_index = self._build_label_index(stmts)
                if target in local_index:
                    pc = local_index[target]
                    continue  # 从标签位置继续，不 pc+1
                else:
                    # 当前层级没有该标签，向上传播
                    raise
            except _ReturnSignal:
                raise  # return 直接穿透，不记错误日志
            except _BreakSignal:
                raise  # break 直接穿透，由 loop/for 处理
            except BaseException as e:
                line_info = f"(行 {node.line_no})" if hasattr(node, 'line_no') and node.line_no else ""
                logger.error(f"DSL 执行异常 {line_info}: {e}")
                logger.error(f"异常详情:\n{traceback.format_exc()}")
                raise  # 异常即终止，不跳过继续

            pc += 1

    def _exec_stmt(self, node):
        """执行单条语句"""
        # 语句边界也检查停止标志，让 F10 在两条语句之间立即生效
        if self._stop_check():
            raise _BreakSignal()
        match node:
            case Click():
                self._exec_click(node)
            case Drag():
                self._exec_drag(node)
            case Wait():
                self._exec_wait(node)
            case Align():
                self._exec_align(node)
            case Scan():
                self._exec_scan(node)
            case Recognize():
                self._exec_recognize(node)
            case Collect():
                self._exec_collect(node)
            case Log():
                if isinstance(node.message, FuncCall):
                    logger.info(str(self._call_func(node.message)))
                else:
                    logger.info(self._resolve(node.message))
            case If():
                self._exec_if(node)
            case For():
                self._exec_for(node)
            case ForRange():
                self._exec_for_range(node)
            case Loop():
                self._exec_loop(node)
            case Break():
                raise _BreakSignal()
            case Return():
                raise _ReturnSignal()
            case Label():
                pass  # 标签无操作
            case Goto():
                raise _GotoSignal(node.target)
            case Eval():
                self._exec_eval(node)
            case EvalFieldChainAssign():
                self._exec_eval_field_assign(node)
            case Call():
                self._exec_call(node)
            case _:
                logger.error(f"未知节点类型: {type(node).__name__}")

    # ─── 基础指令 ─────────────────────────────────────────

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
        if isinstance(node.target, SceneRef):
            # 解析 scene（可能是 str 或 VarRef）
            if isinstance(node.target.scene, VarRef):
                scene = self.variables.get(node.target.scene.name)
                if scene is None:
                    logger.error(f"变量 ${node.target.scene.name} 未定义，无法点击")
                    return
            else:
                scene = node.target.scene
            
            # 解析 region（可能是 str 或 VarRef）
            region = node.target.region
            if isinstance(region, VarRef):
                region_val = self.variables.get(region.name)
                if region_val is None:
                    logger.error(f"变量 ${region.name} 未定义，无法点击")
                    return
                # 尝试从 coord_meta 查找该 key 对应的 Region
                region_obj = self._find_region_in_coord_meta(region_val)
                if region_obj is not None:
                    x, y = self._ensure_workflow()._region_to_screen(region_obj, jitter=True)
                    if x is not None and y is not None:
                        self._input.click_screen(x, y, f"{scene}/{region_val}")
                        return
                # 回退：作为 region key 名查场景配置
                self._ensure_workflow().click_any(str(scene), str(region_val))
            else:
                self._ensure_workflow().click_any(str(scene), region)
        else:
            logger.error(f"click: 未知目标类型 {type(node.target).__name__}")

    def _exec_drag(self, node: Drag):
        """drag scene.arrow / scene.panel[row][col] — scene 和 arrow 都可以是常量或变量。
        若为坐标模式（from_point/to_point），则两端点按画布归一化坐标反算。
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
        if isinstance(node.scene, PanelGridDrag):
            # grid 级拖拽：起点为 panel 中心，距离按整行/列高度
            grid = node.scene
            panel_obj = self._find_panel_in_layout(grid.scene, grid.panel)
            if panel_obj is None:
                logger.error(f"drag grid: 未找到 panel {grid.scene}.{grid.panel}")
                return
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
                logger.error(f"drag grid: 距离无效: {distance}")
                return
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
            # panel drag：根据方向和距离计算拖拽终点
            # up = 手指向上划 = 内容下移 = 显示上方内容
            # down = 手指向下划 = 内容上移 = 显示下方内容
            # left = 手指向左划 = 内容右移 = 显示左侧内容
            # right = 手指向右划 = 内容左移 = 显示右侧内容
            panel_obj = self._find_panel_in_layout(node.scene.scene, node.scene.panel)
            if panel_obj is None:
                return
            # 解析 distance（支持 int、float、VarRef）
            distance = node.distance
            if isinstance(distance, VarRef):
                distance = self.variables.get(distance.name, 1.0)
            try:
                distance = float(distance)  # 支持浮点数（如 0.5 表示半行）
            except (TypeError, ValueError):
                logger.error(f"drag: 距离无效: {distance}")
                return
            w, h = self._capture.get_capture_size()
            canvas = self._layout.get_canvas()
            direction = node.direction or "down"
            # 距离基于 align 实测的 slot + span/2（与 grid drag 一致）
            cache_key = (node.scene.scene, node.scene.panel)
            cal = self._panel_alignments.get(cache_key)
            # _panel_ref_to_screen 已触发懒加载，此处 cal 应有效
            if cal is None:
                logger.error(f"drag panel: align 失败: {node.scene.scene}.{node.scene.panel}")
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
        if isinstance(node.scene, SceneRef):
            # 解析 scene（可能是 str 或 VarRef）
            if isinstance(node.scene.scene, VarRef):
                scene = self.variables.get(node.scene.scene.name)
                if scene is None:
                    logger.error(f"变量 ${node.scene.scene.name} 未定义，无法拖拽")
                    return
            else:
                scene = node.scene.scene
            
            # 解析 arrow（可能是 str 或 VarRef）
            arrow = node.scene.region
            if isinstance(arrow, VarRef):
                arrow_val = self.variables.get(arrow.name)
                if arrow_val is None:
                    logger.error(f"变量 ${arrow.name} 未定义，无法拖拽")
                    return
                arrow = arrow_val
            
            duration = self._resolve_duration(node.duration) if node.duration else None
            hold = node.hold
            self._ensure_workflow().drag_arrow(str(scene), str(arrow), duration=duration, hold=hold)
        else:
            logger.error(f"drag: 未知目标类型 {type(node.scene).__name__}")

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
                logger.error(f"wait ${delay.name} 不是数值或范围类型: {val}")
        elif isinstance(delay, Literal):
            val = delay.value
            if isinstance(val, (int, float)):
                self._ensure_workflow().wait_seconds(float(val))
            else:
                # 命名延迟
                self._ensure_workflow().wait_delay(str(val))
        else:
            self._ensure_workflow().wait_delay(str(delay))

    # ─── Panel 对齐与路由 ────────────────────────────────────

    def _exec_align(self, node: "Align"):
        """align [scene].[panel] — 截图 panel 区域 + 运行图像自对齐，缓存 slot 中心"""
        scene_key = node.scene
        panel_key = node.panel
        panel_obj = self._find_panel_in_layout(scene_key, panel_key)
        if panel_obj is None:
            logger.error(f"align: 场景 {scene_key} 未找到 panel {panel_key}")
            return
        # 截取 panel 区域图像
        panel_img = self._capture_panel_image(panel_obj)
        if panel_img is None:
            logger.error(f"align: 无法截取 panel {scene_key}.{panel_key}")
            return
        # 运行图像自对齐
        alignment = detect_grid(panel_img, expected_rows=panel_obj.rows, expected_cols=panel_obj.cols)
        if alignment is None:
            logger.error(f"align: panel {scene_key}.{panel_key} 未检测到 slot")
            return
        self._panel_alignments[(scene_key, panel_key)] = alignment
        logger.info(
            f"align: {scene_key}.{panel_key} 已对齐，"
            f"检测到 {alignment.total_slots} 个 slot 中心"
        )

    def _panel_ref_to_screen(self, ref: PanelRef) -> tuple[int | None, int | None]:
        """PanelRef → 屏幕绝对坐标

        若缓存未命中，自动触发一次 align。
        """
        scene_key = ref.scene
        panel_key = ref.panel
        # 解析 row/col（支持 int 字面量或 $var）
        row = self._resolve(ref.row) if isinstance(ref.row, VarRef) else ref.row
        col = self._resolve(ref.col) if isinstance(ref.col, VarRef) else ref.col
        try:
            row_idx = int(float(row)) - 1  # DSL 1-based → 0-based
            col_idx = int(float(col)) - 1
        except (TypeError, ValueError):
            logger.error(f"panel 索引非数值: row={row}, col={col}")
            return None, None

        # 缓存未命中 → 自动 align
        cache_key = (scene_key, panel_key)
        if cache_key not in self._panel_alignments:
            logger.info(f"panel 缓存未命中，自动 align: {scene_key}.{panel_key}")
            auto_node = Align(scene=scene_key, panel=panel_key)
            self._exec_align(auto_node)
        cal = self._panel_alignments.get(cache_key)
        if cal is None:
            logger.error(f"panel 未对齐: {scene_key}.{panel_key}")
            return None, None

        panel_obj = self._find_panel_in_layout(scene_key, panel_key)
        if panel_obj is None:
            return None, None

        # 查表：用对齐结果的行列数做越界检查
        if not (0 <= row_idx < cal.n_rows and 0 <= col_idx < cal.n_cols):
            logger.debug(f"panel 索引越界: [{row_idx + 1}][{col_idx + 1}]，"
                         f"对齐结果 {cal.n_rows}×{cal.n_cols}")
            return None, None

        cx, cy = cal.slot_center(row_idx, col_idx)  # 相对于 panel 区域
        return self._panel_ratio_to_screen(panel_obj, cx, cy)

    def _crop_slot_image(self, ref: PanelRef) -> "tuple[np.ndarray | None, str, int, int]":
        """裁剪 panel cell 图像，返回 (slot_img, slot_key, row_idx, col_idx)

        slot_key 格式: "r{row}c{col}"（1-based），用于结果字典的 key。
        """
        scene_key = ref.scene
        panel_key = ref.panel
        row = self._resolve(ref.row) if isinstance(ref.row, VarRef) else ref.row
        col = self._resolve(ref.col) if isinstance(ref.col, VarRef) else ref.col
        try:
            row_idx = int(float(row)) - 1
            col_idx = int(float(col)) - 1
        except (TypeError, ValueError):
            logger.error(f"panel 索引非数值: row={row}, col={col}")
            return None, "", 0, 0

        # 自动 align
        cache_key = (scene_key, panel_key)
        if cache_key not in self._panel_alignments:
            auto_node = Align(scene=scene_key, panel=panel_key)
            self._exec_align(auto_node)
        cal = self._panel_alignments.get(cache_key)
        panel_obj = self._find_panel_in_layout(scene_key, panel_key)
        if cal is None or panel_obj is None:
            logger.error(f"panel 未对齐: {scene_key}.{panel_key}")
            return None, "", 0, 0

        if not (0 <= row_idx < cal.n_rows and 0 <= col_idx < cal.n_cols):
            logger.debug(f"panel 索引越界: [{row_idx + 1}][{col_idx + 1}]，"
                         f"对齐结果 {cal.n_rows}×{cal.n_cols}")
            return None, "", 0, 0

        # 截取 panel 图像
        panel_img = self._capture_panel_image(panel_obj)
        if panel_img is None:
            return None, "", 0, 0

        ph, pw = panel_img.shape[:2]
        # 用对齐的实际边界裁剪（非等分）
        x1_r, y1_r, x2_r, y2_r = cal.slot_bounds(row_idx, col_idx)
        x1 = max(0, int(x1_r * pw))
        y1 = max(0, int(y1_r * ph))
        x2 = min(pw, int(x2_r * pw))
        y2 = min(ph, int(y2_r * ph))
        slot_img = panel_img[y1:y2, x1:x2]
        if slot_img.size == 0:
            logger.error(f"slot 裁剪为空: {scene_key}.{panel_key}[{row_idx+1}][{col_idx+1}]")
            return None, "", 0, 0
        slot_key = f"r{row_idx + 1}c{col_idx + 1}"
        return slot_img, slot_key, row_idx, col_idx

    def _scan_panel_cell(self, node: Scan):
        """scan [scene].[panel][row][col] as $var [by ...]"""
        ref: PanelRef = node.scene
        var_name = node.target.name if isinstance(node.target, VarRef) else str(node.target)
        slot_img, slot_key, _, _ = self._crop_slot_image(ref)
        if slot_img is None:
            self.variables[var_name] = ""
            return
        if node.by is not None:
            # by 子句：短路匹配，返回是否命中
            by_clause: ByClause = node.by
            target_value = self._resolve(by_clause.target)
            ocr_results = self._ocr.recognize(slot_img)
            text = " ".join(r.text for r in ocr_results).strip()
            matched = self._match_text(text, target_value, by_clause.match_mode)
            self.variables[var_name] = text if matched else ""
        else:
            ocr_results = self._ocr.recognize(slot_img)
            text = " ".join(r.text for r in ocr_results).strip()
            self.variables[var_name] = {slot_key: text} if text else {slot_key: ""}
        logger.info(f"scan panel cell [{ref.scene}.{ref.panel}][{slot_key}] => {self.variables[var_name]}")

    def _recognize_panel_cell(self, node: Recognize):
        """recognize [scene].[panel][row][col] as $var [by ...] [on group ...]"""
        ref: PanelRef = node.scene
        var_name = node.target.name if isinstance(node.target, VarRef) else str(node.target)
        slot_img, slot_key, _, _ = self._crop_slot_image(ref)
        if slot_img is None:
            self.variables[var_name] = ""
            return
        group = self._resolve(node.group) if node.group is not None else None
        if node.by is not None:
            by_clause: ByClause = node.by
            target_value = self._resolve(by_clause.target)
            info = self._ensure_workflow().material_recognizer.recognize(slot_img, group=group)
            matched = self._match_text(info.type, target_value, by_clause.match_mode)
            self.variables[var_name] = info.type if matched else ""
        else:
            info = self._ensure_workflow().material_recognizer.recognize(slot_img, group=group)
            self.variables[var_name] = {slot_key: info.type}
        logger.info(f"recognize panel cell [{ref.scene}.{ref.panel}][{slot_key}] => {self.variables[var_name]}")

    def _match_text(self, text: str, target: str, mode: str) -> bool:
        """文本匹配（用于 by 子句短路识别）"""
        if mode == "equals":
            return text == target
        if mode == "contains":
            return target in text
        if mode == "equals_any":
            return text in target if isinstance(target, list) else text == target
        if mode == "contains_any":
            items = target if isinstance(target, list) else [target]
            return any(t in text for t in items)
        return False

    def _find_panel_in_layout(self, scene_key: str, panel_key: str):
        """在 layout 中查找指定 panel 实例"""
        panels = self._layout.get_scene_panels(scene_key)
        return next((p for p in panels if p.key == panel_key), None)

    def _capture_panel_image(self, panel_obj) -> "np.ndarray | None":
        """截取 panel 区域图像（像素数组），用于校准"""
        import numpy as np
        full = self._capture.capture()
        if full is None:
            return None
        w_cap, h_cap = self._capture.get_capture_size()
        if w_cap == 0 or h_cap == 0:
            return None
        canvas = self._layout.get_canvas()
        canvas_x = int(canvas.x_ratio * w_cap)
        canvas_y = int(canvas.y_ratio * h_cap)
        canvas_w = int(canvas.w_ratio * w_cap)
        canvas_h = int(canvas.h_ratio * h_cap)
        # panel 在画布内的像素区域
        px = canvas_x + int(panel_obj.x_ratio * canvas_w)
        py = canvas_y + int(panel_obj.y_ratio * canvas_h)
        pw = max(1, int(panel_obj.w_ratio * canvas_w))
        ph = max(1, int(panel_obj.h_ratio * canvas_h))
        # 裁剪
        h_img, w_img = full.shape[:2]
        x1 = max(0, min(px, w_img))
        y1 = max(0, min(py, h_img))
        x2 = max(0, min(px + pw, w_img))
        y2 = max(0, min(py + ph, h_img))
        if x2 <= x1 or y2 <= y1:
            return None
        return full[y1:y2, x1:x2].copy()

    def _panel_ratio_to_screen(
        self, panel_obj, cx_panel: float, cy_panel: float
    ) -> tuple[int, int]:
        """panel 内归一化坐标 → 屏幕绝对坐标"""
        w, h = self._capture.get_capture_size()
        canvas = self._layout.get_canvas()
        canvas_x = canvas.x_ratio * w
        canvas_y = canvas.y_ratio * h
        canvas_w = canvas.w_ratio * w
        canvas_h = canvas.h_ratio * h
        sx = canvas_x + (panel_obj.x_ratio + cx_panel * panel_obj.w_ratio) * canvas_w
        sy = canvas_y + (panel_obj.y_ratio + cy_panel * panel_obj.h_ratio) * canvas_h
        return int(self._window_left + sx), int(self._window_top + sy)

    def _exec_scan(self, node: Scan):
        # PanelRef: panel cell 级 OCR
        if isinstance(node.scene, PanelRef):
            self._scan_panel_cell(node)
            return
        # 解析场景名（可能是 str 或 VarRef）
        scene_ref = node.scene.scene if isinstance(node.scene, SceneRef) else node.scene
        if isinstance(scene_ref, VarRef):
            scene = self.variables.get(scene_ref.name, "")
        else:
            scene = str(scene_ref)
        field_keys = None
        if node.fields:
            field_keys = [self._resolve(f) for f in node.fields]
        elif node.region_var:
            # 动态 region：[scene].$var → 解析变量值
            region_key = self._resolve(node.region_var)
            if isinstance(region_key, list):
                # 列表变量：展开为多字段 key
                field_keys = [str(k) for k in region_key]
            else:
                field_keys = [str(region_key)]
        var_name = node.target.name if isinstance(node.target, VarRef) else str(node.target)

        if node.by is not None:
            # ── by 子句：短路 OCR，返回字段名 str ──
            by_clause: ByClause = node.by
            target_value = self._resolve(by_clause.target)
            result = self._ensure_workflow().ocr_scene_by(scene, field_keys or [], target_value, by_clause.match_mode)
            self.variables[var_name] = result  # str（命中字段名或 ""）
        else:
            result = self._ensure_workflow().ocr_scene(scene, field_keys)
            self.variables[var_name] = result  # dict
            # 存 region 元数据，供 click [scene].$key 解析坐标
            regions = self._layout.get_scene_regions(scene)
            if field_keys:
                regions = [r for r in regions if r.key in field_keys]
            self._coord_meta[var_name] = {r.key: r for r in regions}

    def _exec_recognize(self, node: Recognize):
        """recognize [scene].[f1, f2, ...] as $var [by ...] [group ...] — 图像识别场景中的材料"""
        # PanelRef: panel cell 级材料识别
        if isinstance(node.scene, PanelRef):
            self._recognize_panel_cell(node)
            return
        # 解析场景名（可能是 str 或 VarRef）
        scene_ref = node.scene.scene if isinstance(node.scene, SceneRef) else node.scene
        if isinstance(scene_ref, VarRef):
            scene = self.variables.get(scene_ref.name, "")
        else:
            scene = str(scene_ref)
        var_name = node.target.name if isinstance(node.target, VarRef) else str(node.target)
        field_keys = None
        if node.fields:
            field_keys = [self._resolve(f) for f in node.fields]
        elif node.region_var:
            # 动态 region：[scene].$var → 解析变量值
            region_key = self._resolve(node.region_var)
            if isinstance(region_key, list):
                # 列表变量：展开为多字段 key
                field_keys = [str(k) for k in region_key]
            else:
                field_keys = [str(region_key)]

        # 解析可选的 group 子句
        group = None
        if node.group is not None:
            group = self._resolve(node.group)

        if node.by is not None:
            # ── by 子句：短路材料识别，返回 slot 名 str ──
            by_clause: ByClause = node.by
            target_value = self._resolve(by_clause.target)
            result = self._ensure_workflow().recognize_materials_by(scene, field_keys or [], target_value, by_clause.match_mode, group=group)
            self.variables[var_name] = result  # str（命中 slot 名或 ""）
        else:
            result, region_map = self._ensure_workflow().recognize_materials(scene, field_keys, group=group)
            self.variables[var_name] = result           # {slot_key: "材料类型"}
            self._coord_meta[var_name] = region_map     # {slot_key: Region}

    def _exec_collect(self, node: Collect):
        """collect $var | field_access | literal [as "label" | as $alias_var] — 将值存入输出 dict"""
        if isinstance(node.source, VarRef):
            var_name = node.source.name
            value = self.variables.get(var_name)
            if value is None:
                logger.warning(f"collect: 变量 ${var_name} 未定义，跳过")
                return
            default_key = var_name
        elif isinstance(node.source, FieldAccess):
            value = self._resolve(node.source)
            # 默认 key 取最后一级字段名（如 session.equipped → "equipped"）
            fn = node.source.field_name
            if isinstance(fn, str):
                default_key = fn
            elif isinstance(fn, Literal):
                default_key = str(fn.value)
            else:
                default_key = self._field_path(node.source)
        elif isinstance(node.source, Literal):
            value = node.source.value
            default_key = "value"
        else:
            logger.warning(f"collect: 不支持的源类型 {type(node.source).__name__}")
            return
        # 解析 key：优先 alias_var（动态），其次 alias（静态），最后 default_key
        if node.alias_var:
            key = self.variables.get(node.alias_var.name, default_key)
        elif node.alias:
            key = node.alias
        else:
            key = default_key
        self.output[key] = value
        logger.debug(f"collect: {key} = {value}")

    def _exec_eval(self, node: Eval):
        # 字面量赋值快捷路径：eval $var = "str" | 123 | -1.5
        if node.func_name == "__literal__":
            lit_val = node.func_args[0].value
            if node.target is not None:
                self.variables[node.target] = lit_val
                logger.debug(f"eval: {node.target} = {lit_val!r}")
            return

        # 空字典初始化：eval $var = {}
        if node.func_name == "__empty_dict__":
            if node.target is not None:
                self.variables[node.target] = {}
                logger.debug(f"eval: {node.target} = {{}}")
            return

        # 列表赋值：eval $var = ["a", "b", $c]
        if node.func_name == "__list__":
            if node.target is not None:
                list_val = [self._resolve(item) for item in node.func_args]
                self.variables[node.target] = list_val
                logger.debug(f"eval: {node.target} = {list_val!r}")
            return

        # 范围元组赋值：eval $var = (1, 2) 或 $var = (1, 2)
        if node.func_name == "__range__":
            if node.target is not None:
                range_val = node.func_args[0].value  # (float, float)
                self.variables[node.target] = range_val
                logger.debug(f"eval: {node.target} = {range_val!r}")
            return

        # 默认值赋值：default $var = value — 仅当变量未从外部传入时才赋值
        if node.func_name == "__default__":
            if node.target is not None and node.target not in self.variables:
                default_val = node.func_args[0].value
                self.variables[node.target] = default_val
                logger.debug(f"default: {node.target} = {default_val!r}")
            return

        # 表达式赋值：eval $var = $dict.$key | $other_var
        if node.func_name == "__expr__":
            expr_node = node.func_args[0]
            if isinstance(expr_node, FieldAccess):
                val = self._eval_field_raw(expr_node)
            else:
                val = self._resolve(expr_node)
            if node.target is not None:
                self.variables[node.target] = val
                logger.debug(f"eval: {node.target} = {val!r}")
            return

        # 函数调用路径
        result = self._call_func_from_eval(node)

        if node.target is not None:
            self.variables[node.target] = result
            logger.debug(f"eval: {node.target} = {result}")
        else:
            logger.debug(f"eval: {node.func_name}(...) = {result} (丢弃)")

    def _exec_eval_field_assign(self, node: EvalFieldChainAssign):
        """eval $dict.key = value 或 eval session.key = value — 字段赋值

        支持 VarRef 根（$dict.key）和 KeywordRef 根（session.key / context.key）。
        """
        # 解析字段访问链，获取所有字段名
        field_chain = self._extract_field_chain(node.target)
        if not field_chain:
            logger.error("eval_field_assign: 无法解析字段链")
            return

        # 第一个字段是变量名或关键字名
        root_name = field_chain[0]
        # 判断根是关键字还是普通变量
        if root_name == "session":
            dict_var = self.session
        elif root_name == "context":
            dict_var = self.context
        else:
            dict_var = self.variables.get(root_name)
        if not isinstance(dict_var, dict):
            logger.error(f"eval_field_assign: {root_name} 不是字典类型")
            return

        # 遍历到倒数第二个字段，获取父字典
        current = dict_var
        for field_name in field_chain[1:-1]:
            if not isinstance(current, dict):
                logger.error(f"eval_field_assign: 中间字段 {field_name} 不是字典类型")
                return
            next_val = current.get(field_name)
            if next_val is None:
                # 自动创建空字典
                next_val = {}
                current[field_name] = next_val
            current = next_val

        # 最后一个字段是赋值目标
        final_field = field_chain[-1]
        # 解析右侧值：FuncCall 调用函数，空 dict 初始化，其余统一走 _resolve
        # （_resolve 覆盖 VarRef / Literal / FieldAccess，后者用于 $a.b = $c.d.e）
        if isinstance(node.value, FuncCall):
            value = self._call_func(node.value)
        elif isinstance(node.value, dict):
            value = {}
        else:
            value = self._resolve(node.value)
        current[final_field] = value
        logger.debug(f"eval: {'.'.join(field_chain)} = {value!r}")

    def _extract_field_chain(self, node: FieldAccess) -> list:
        """从 FieldAccess 节点提取字段名链

        支持 VarRef / KeywordRef 作为根。返回的链第一个元素是变量名或关键字名。
        """
        chain = []
        current = node
        while isinstance(current, FieldAccess):
            fn = current.field_name
            if isinstance(fn, str):
                chain.append(fn)
            elif isinstance(fn, VarRef):
                # 动态字段名，解析变量值
                chain.append(self.variables.get(fn.name, ""))
            elif isinstance(fn, Literal):
                chain.append(fn.value)
            elif isinstance(fn, KeywordRef):
                chain.append(fn.name)
            current = current.root
        # 最底层是 VarRef（变量名）或 KeywordRef（关键字名）
        if isinstance(current, VarRef):
            chain.append(current.name)
        elif isinstance(current, KeywordRef):
            chain.append(current.name)
        chain.reverse()
        return chain

    def _call_func(self, node: FuncCall):
        """执行函数调用并返回结果"""
        resolved_args = [self._resolve(arg) for arg in node.func_args]
        return self._ensure_workflow().call_function(node.func_name, resolved_args, engine=self)

    def _call_func_from_eval(self, node: Eval):
        """从 Eval 节点执行函数调用"""
        resolved_args = [self._resolve(arg) for arg in node.func_args]
        return self._ensure_workflow().call_function(node.func_name, resolved_args, engine=self)

    def _exec_call(self, node: Call):
        """call "sub.wf" with $x as "arg1" read "key" as $var

        创建独立子 engine，天然隔离变量空间，支持任意深度嵌套。
        相对路径基于当前 wf 所在目录解析。
        """
        wf_path_str = self._resolve(node.workflow)
        wf_path = Path(wf_path_str)
        if not wf_path.is_absolute() and self._base_dir:
            wf_path = self._base_dir / wf_path
        logger.info(f"--- call 子工作流: {wf_path} ---")

        # 1. 从父 context 取参数值，注入子 engine
        arg_values = {}
        for left, right in node.args:
            val = self._resolve(left)
            key = self._resolve(right)  # 右侧取值：$var → 变量值，"str" → 字符串
            arg_values[str(key)] = val

        # 2. 创建独立子 engine + 注入参数
        sub_engine = WorkflowEngine(
            capture=self._capture, ocr=self._ocr, input_ctrl=self._input,
            layout=self._layout, delay_config=self._delay,
            window_left=self._window_left, window_top=self._window_top,
            stop_check=self._stop_check,
        )
        sub_engine.variables = dict(arg_values)
        # coord_meta 全局共享：子工作流可访问父工作流 scan/recognize 的坐标元数据
        sub_engine._coord_meta = self._coord_meta
        # session/context 透传：子工作流可读写同一份持久/临时状态
        sub_engine.session = self.session
        sub_engine.context = self.context

        # 3. 运行子 wf（不重置 context，保持与父工作流的共享）
        sub_output = sub_engine.execute(wf_path, _reset_context=False)

        # 4. 从子 engine output 提取 read 结果，写入父 context
        count = 0
        for left, right in node.reads:
            key = self._resolve(left)
            target_name = self._resolve_var_name(right)  # 右侧取变量名：$var → 变量名，"str" → 字符串
            if isinstance(sub_output, dict) and key in sub_output:
                self.variables[target_name] = sub_output[key]
                count += 1
            else:
                logger.warning(f"call: 子工作流 output 中无 key '{key}'")

        logger.info(f"--- call 返回，取回 {count} 个值 ---")

    # ─── 控制流 ───────────────────────────────────────────

    def _exec_if(self, node: If):
        cond_result = self._eval_condition(node.condition)
        logger.debug(f"if 条件求值: {self._cond_desc(node.condition)} -> {cond_result}")

        if cond_result:
            self._exec_body(node.then_body)
        elif node.else_body:
            self._exec_body(node.else_body)

    def _exec_for(self, node: For):
        # 解析迭代列表：支持静态列表、动态变量和函数调用
        if isinstance(node.iterable, VarRef):
            # 动态迭代：for $x in $list_var
            raw = self.variables.get(node.iterable.name)
            if raw is None:
                logger.error(f"for: 变量 ${node.iterable.name} 未定义")
                return
            if not isinstance(raw, list):
                logger.error(f"for: ${node.iterable.name} 不是列表类型，无法迭代")
                return
            items = raw
        elif isinstance(node.iterable, FuncCall):
            # 函数调用迭代：for i in range(1, 100)
            items = self._call_func(node.iterable)
            if not isinstance(items, list):
                logger.error(f"for: 函数 {node.iterable.func_name}() 返回值不是列表类型")
                return
        else:
            # 静态迭代：for $x in [a, b, c]
            items = [self._resolve(item) for item in node.iterable]
        logger.debug(f"for {node.var} in {items}")

        for value in items:
            if self._stop_check():
                logger.info("工作流被用户停止")
                return
            # 设置循环变量
            self.variables[node.var] = str(value)
            try:
                self._exec_body(node.body)
            except _BreakSignal:
                break

    def _exec_for_range(self, node: ForRange):
        """for i in [start...end] — 闭区间范围迭代"""
        start_val = self._resolve(node.start)
        end_val = self._resolve(node.end)
        try:
            start_int = int(float(start_val))
            end_int = int(float(end_val))
        except (TypeError, ValueError):
            logger.error(f"for_range: 起止值非数值: start={start_val}, end={end_val}")
            return
        if start_int > end_int:
            logger.error(f"for_range: 起始值大于结束值: {start_int} > {end_int}")
            return
        items = list(range(start_int, end_int + 1))  # 闭区间
        logger.debug(f"for {node.var} in [{start_int}...{end_int}] → {items}")

        for value in items:
            if self._stop_check():
                logger.info("工作流被用户停止")
                return
            self.variables[node.var] = str(value)
            try:
                self._exec_body(node.body)
            except _BreakSignal:
                break

    def _exec_loop(self, node: Loop):
        # 解析循环次数
        if isinstance(node.count, int):
            count = node.count
        elif isinstance(node.count, VarRef):
            # 变量引用：loop $execute_times
            val = self.variables.get(node.count.name)
            count = int(val) if val is not None else 0
        else:
            # 字符串（NAME）
            val = self.variables.get(str(node.count))
            count = int(val) if val is not None else 0

        logger.debug(f"loop {count}")
        for _ in range(count):
            if self._stop_check():
                logger.info("工作流被用户停止")
                return
            try:
                self._exec_body(node.body)
            except _BreakSignal:
                break

    # ─── 条件求值 ─────────────────────────────────────────

    def _eval_condition(self, node) -> bool:
        """递归求值条件表达式 AST 节点"""
        match node:
            case Contains():
                left = self._eval_var_or_field(node.left)
                right = self._resolve(node.right)
                return str(right) in str(left) if left else False
            case Equals():
                left = self._eval_var_or_field(node.left)
                right = self._resolve(node.right)
                return str(left) == str(right)
            case InList():
                left = self._eval_var_or_field(node.left)
                right = [str(self._resolve(item)) for item in node.right]
                return str(left) in right if left else False
            case IsEmpty():
                left = self._eval_var_or_field(node.expr)
                return not left or str(left).strip() == ""
            case GreaterThan():
                left = self._eval_var_or_field(node.left)
                num = self._to_number(left)
                return num > node.right if num is not None else False
            case LessThan():
                left = self._eval_var_or_field(node.left)
                num = self._to_number(left)
                return num < node.right if num is not None else False
            case GreaterEqual():
                left = self._eval_var_or_field(node.left)
                num = self._to_number(left)
                return num >= node.right if num is not None else False
            case LessEqual():
                left = self._eval_var_or_field(node.left)
                num = self._to_number(left)
                return num <= node.right if num is not None else False
            case NotEqual():
                left = self._eval_var_or_field(node.left)
                num = self._to_number(left)
                return num != node.right if num is not None else True
            case NumericEqual():
                left = self._eval_var_or_field(node.left)
                num = self._to_number(left)
                return num == node.right if num is not None else False
            case Not():
                return not self._eval_condition(node.operand)
            case And():
                return self._eval_condition(node.left) and self._eval_condition(node.right)
            case Or():
                return self._eval_condition(node.left) or self._eval_condition(node.right)
            case VarRef():
                # 条件中的 $var → truthy 检查
                val = self.variables.get(node.name)
                return bool(val)
            case _:
                logger.error(f"未知条件节点: {type(node).__name__}")
                return False

    @staticmethod
    def _to_number(val: str):
        """将字符串转为数值，失败时返回 None"""
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _field_path(node) -> str:
        """生成字段访问路径的调试描述：$ring.affix_1.value → 'ring.affix_1.value'
        支持 VarRef / KeywordRef 作为根。
        """
        # 裸变量/关键字直接返回名称
        if isinstance(node, VarRef):
            return node.name
        if isinstance(node, KeywordRef):
            return node.name
        parts = []
        current = node
        while isinstance(current, FieldAccess):
            fn = current.field_name
            if isinstance(fn, VarRef):
                parts.append(f"${fn.name}")
            elif isinstance(fn, KeywordRef):
                parts.append(fn.name)
            elif isinstance(fn, Literal):
                parts.append(f'"{fn.value}"')
            else:
                parts.append(str(fn))
            current = current.root
        if isinstance(current, VarRef):
            parts.append(current.name)
        elif isinstance(current, KeywordRef):
            parts.append(current.name)
        return ".".join(reversed(parts))

    def _eval_field_access(self, node: FieldAccess) -> str:
        """求值字段访问链：$var.f1.f2.f3 → 逐层遍历，返回字符串"""
        return str(self._eval_field_raw(node))

    def _eval_var_or_field(self, node) -> str:
        """求值变量或字段访问：支持 VarRef 和 FieldAccess"""
        if isinstance(node, VarRef):
            return str(self.variables.get(node.name, ""))
        elif isinstance(node, FieldAccess):
            return self._eval_field_access(node)
        return ""

    def _eval_field_raw(self, node: FieldAccess):
        """求值字段访问链，返回原始值（dict/int/float/str 等）

        中间层返回 dict/list 以便继续链式访问，叶子层返回具体值。
        root 支持 VarRef / KeywordRef / FieldAccess。
        field_name 支持四种类型：
          str      → 静态 dict key（裸 NAME）
          VarRef   → 动态 key（变量解析后查 dict / 按 index 取 list）
          Literal  → 静态字面量 key（来自 $var."key" 或 $var.[key]）
          KeywordRef → 关键字引用（嵌套 session/context 访问）
        """
        # 先解析 root
        if isinstance(node.root, VarRef):
            current = self.variables.get(node.root.name)
        elif isinstance(node.root, KeywordRef):
            current = self._resolve(node.root)
        elif isinstance(node.root, FieldAccess):
            current = self._eval_field_raw(node.root)
        else:
            return ""

        # 解析当前层 key
        if isinstance(node.field_name, str):
            key = node.field_name
        elif isinstance(node.field_name, VarRef):
            key = self.variables.get(node.field_name.name, "")
        elif isinstance(node.field_name, Literal):
            key = node.field_name.value
        elif isinstance(node.field_name, KeywordRef):
            # field_access.session / field_access.context（罕见但合法）
            return self._resolve(node.field_name)
        else:
            return ""

        # dict 按 key 取
        if isinstance(current, dict):
            return current.get(key, "")
        # list 按 index 取（key 需为整数）
        if isinstance(current, list):
            try:
                idx = int(key)
                return current[idx] if 0 <= idx < len(current) else ""
            except (ValueError, TypeError):
                return ""
        # str 类型不支持字段访问（by 子句返回 str，用户误用 .field 时应报错）
        if isinstance(current, str):
            var_desc = self._field_path(node)
            raise WorkflowUserError(
                f"${var_desc} 的值是字符串类型（{current!r}），"
                f"不能使用 .{key} 访问字段。"
                f"by 子句返回的是字段名（str），不是 dict。"
            )
        return ""

    # ─── 变量解析 ─────────────────────────────────────────

    def _resolve(self, node) -> Any:
        """解析表达式节点为运行时值

        VarRef → 查变量表（找不到则返回 name 本身作为字面量回退）
        KeywordRef → 返回 session/context 字典引用
        Literal → 直接返回值
        FieldAccess → 逐层遍历字典/列表
        list 类型变量原样返回（支持 for 迭代）
        """
        match node:
            case VarRef():
                val = self.variables.get(node.name)
                if val is not None:
                    return val  # 保留原始类型（包括 list）
                # 回退：未定义的变量视为字面量
                return node.name
            case KeywordRef():
                if node.name == "session":
                    return self.session
                if node.name == "context":
                    return self.context
                return {}
            case Literal():
                return node.value
            case FieldAccess():
                return self._eval_field_raw(node)
            case _:
                return str(node) if node is not None else ""

    def _resolve_param(self, node) -> str:
        """解析 click/scan 参数

        VarRef → 变量优先，回退字面量
        Literal → 直接返回
        """
        return self._resolve(node)

    def _resolve_var_name(self, node) -> str:
        """提取变量名（用于 scan as / collect_as 等赋值目标）"""
        if isinstance(node, VarRef):
            return node.name
        return str(node)

    def _resolve_duration(self, node) -> float | tuple[float, float]:
        """解析拖拽时长：Literal → float，list[Literal] → tuple"""
        if isinstance(node, list):
            return (float(node[0].value), float(node[1].value))
        if isinstance(node, Literal):
            return float(node.value)
        return float(node)

    def _coord_ratio_to_screen(self, rx: float, ry: float) -> tuple[int, int]:
        """画布归一化坐标 (rx, ry) → 屏幕绝对坐标

        与 _region_to_screen / _point_to_screen 同源的坐标转换链：
        屏幕 = 窗口偏移 + 画布原点 + 归一化比例 × 画布尺寸。
        窗口缩放/移动后回放仍准确。
        """
        w, h = self._capture.get_capture_size()
        canvas = self._layout.get_canvas()
        canvas_px_x = canvas.x_ratio * w
        canvas_px_y = canvas.y_ratio * h
        canvas_px_w = canvas.w_ratio * w
        canvas_px_h = canvas.h_ratio * h
        cx = canvas_px_x + rx * canvas_px_w
        cy = canvas_px_y + ry * canvas_px_h
        return int(self._window_left + cx), int(self._window_top + cy)

    # ─── coord_meta 查找 ─────────────────────────────────────

    def _find_region_in_coord_meta(self, key: str):
        """在所有 coord_meta 条目中查找 key 对应的 Region

        scan/recognize 会将 {key: Region} 存入 coord_meta，
        此方法遍历所有条目，找到第一个匹配的 Region。
        """
        for var_name, region_map in self._coord_meta.items():
            if isinstance(region_map, dict) and key in region_map:
                return region_map[key]
        return None

    # ─── 工具 ─────────────────────────────────────────────

    @staticmethod
    def _build_label_index(stmts: list) -> dict[str, int]:
        """预扫描语句列表，建立 label → 索引 映射"""
        index = {}
        for i, stmt in enumerate(stmts):
            if isinstance(stmt, Label):
                index[stmt.name] = i
        return index

    @staticmethod
    def _cond_desc(node) -> str:
        """条件的调试描述"""
        match node:
            case Contains():
                return f"{WorkflowEngine._field_path(node.left)} contains {node.right}"
            case Equals():
                return f"{WorkflowEngine._field_path(node.left)} equals {node.right}"
            case InList():
                return f"{WorkflowEngine._field_path(node.left)} in [...]"
            case IsEmpty():
                return f"{WorkflowEngine._field_path(node.expr)} is_empty"
            case GreaterThan():
                return f"{WorkflowEngine._field_path(node.left)} > {node.right}"
            case LessThan():
                return f"{WorkflowEngine._field_path(node.left)} < {node.right}"
            case GreaterEqual():
                return f"{WorkflowEngine._field_path(node.left)} >= {node.right}"
            case LessEqual():
                return f"{WorkflowEngine._field_path(node.left)} <= {node.right}"
            case NotEqual():
                return f"{WorkflowEngine._field_path(node.left)} != {node.right}"
            case NumericEqual():
                return f"{WorkflowEngine._field_path(node.left)} == {node.right}"
            case Not():
                return f"not ({WorkflowEngine._cond_desc(node.operand)})"
            case And():
                return f"({WorkflowEngine._cond_desc(node.left)} and {WorkflowEngine._cond_desc(node.right)})"
            case Or():
                return f"({WorkflowEngine._cond_desc(node.left)} or {WorkflowEngine._cond_desc(node.right)})"
            case VarRef():
                return f"[{node.name}]"
            case _:
                return str(type(node).__name__)
