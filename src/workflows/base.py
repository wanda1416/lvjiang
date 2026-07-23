"""工作流基类 - 提供游戏操作能力

子类可以：
1. 纯 Python 实现：直接重写 run() 方法
2. DSL 驱动：由 WorkflowEngine 加载 .wf 文件执行，操作委托给本实例

所有游戏操作（点击、截图、OCR、等待、变量管理）均在此类中实现，
由 WorkflowEngine 通过 _ensure_workflow() 懒创建并调用。
"""

import math
import random
import time

import numpy as np
from loguru import logger
from pathlib import Path
from typing import Callable, Optional

from ..config import DelayConfig
from ..core.capture_base import CaptureBackend
from ..core.ocr import OCREngine
from ..core.input_base import InputBackend
from ..core.scene_registry import Layout, Point, Region, CanvasConfig
from . import builtins  # noqa: F401  触发内置函数注册
from .align import detect_grid, GridAlignment


class BaseWorkflow:
    """工作流基类

    运行时状态：
    - variables: 用户变量表（scan/eval 赋值，所有数据显式存储）
    - output: 收集的输出数据字典（由 collect 语句写入，key 为 alias 或变量名）
    """

    # 类级别共享：MaterialRecognizer 跨所有实例复用，避免重复加载参考图
    _shared_material_recognizer = None

    def __init__(
        self,
        capture: CaptureBackend,
        ocr: OCREngine,
        input_ctrl: InputBackend,
        layout: Layout,
        delay_config: DelayConfig | None = None,
        window_left: int = 0,
        window_top: int = 0,
        stop_check: Optional[Callable[[], bool]] = None,
    ):
        self._capture = capture
        self._ocr = ocr
        self._input = input_ctrl
        self._layout = layout
        self._delay = delay_config or DelayConfig()
        self._window_left = window_left
        self._window_top = window_top
        self._stop_check = stop_check or (lambda: False)

        # 运行时状态
        self.output: dict = {}  # collect 语句写入的输出字典
        self.variables: dict = {}
        # panel 对齐缓存：(scene_key, panel_key) → GridAlignment
        self._panel_alignments: dict[tuple[str, str], GridAlignment] = {}

    def run(self) -> dict:
        """执行工作流（子类重写）

        Returns:
            dict: collect 累积结果
        """
        raise NotImplementedError

    def reset_state(self):
        """重置运行时状态（在 run 开始前调用）"""
        self.output = {}
        self.variables = {}

    @property
    def is_stopped(self) -> bool:
        """是否请求了停止"""
        return self._stop_check()

    # ─── 点击操作 ──────────────────────────────────────────

    def click_region(self, scene_key: str, field_key: str, jitter: bool = True):
        """点击指定场景中指定区域的中心（带随机抖动）"""
        regions = self._layout.get_scene_regions(scene_key)
        region = next((r for r in regions if r.key == field_key), None)
        if region is None:
            logger.error(f"场景 {scene_key} 没有定义区域: {field_key}")
            return

        screen_x, screen_y = self._region_to_screen(region, jitter)
        if screen_x is None:
            return

        logger.debug(f"点击: {scene_key}/{field_key} -> 屏幕({screen_x},{screen_y})")
        self._input.click_screen(screen_x, screen_y, f"{scene_key}/{field_key}")

    # ─── 截图与 OCR ────────────────────────────────────────

    def ocr_scene(self, scene_key: str, field_keys: list[str] | None = None) -> dict[str, str]:
        """对指定场景执行截图 + OCR

        Args:
            scene_key: 场景 key
            field_keys: 可选，只 OCR 指定字段列表

        Returns:
            {field_key: ocr_text, ...}
        """
        img = self._capture.capture()
        if img is None:
            logger.error("截图失败")
            return {}

        canvas = self._layout.get_canvas()
        regions = self._layout.get_scene_regions(scene_key)
        if not regions:
            logger.warning(f"场景 {scene_key} 没有定义区域")
            return {}

        if field_keys:
            regions = [r for r in regions if r.key in field_keys]

        result = self._ocr.ocr_scene_regions(img, canvas, regions, scene_key)
        fields_display = field_keys if field_keys else [r.key for r in self._layout.get_scene_regions(scene_key)]
        logger.debug(f"OCR [{scene_key}]:{fields_display} => {result}")
        return result

    def click_at(self, x: int, y: int):
        """点击屏幕绝对坐标"""
        logger.debug(f"点击坐标: ({x}, {y})")
        self._input.click_screen(x, y, "dynamic")

    # ─── 材料识别 ──────────────────────────────────────────

    @property
    def material_recognizer(self):
        """延迟构造 MaterialRecognizer（类级别共享，跨工作流运行复用）"""
        if BaseWorkflow._shared_material_recognizer is None:
            from src.apps.yysls.core.material_recognizer import MaterialRecognizer
            BaseWorkflow._shared_material_recognizer = MaterialRecognizer(self._ocr)
        return BaseWorkflow._shared_material_recognizer

    def recognize_materials(
        self,
        scene_key: str,
        slot_keys: list[str] | None = None,
        group: str | None = None,
    ) -> tuple[dict[str, str], dict]:
        """对指定场景的每个 slot 执行材料识别

        Args:
            scene_key: 场景 key
            slot_keys: 可选，只识别指定 slot
            group: 可选，限定材料分组范围

        Returns:
            (result, region_map)
            result: {slot_key: material_type, ...}  空槽为 ""
            region_map: {slot_key: Region, ...}  供 coord_meta 存储
        """
        img = self._capture.capture()
        if img is None:
            logger.error("截图失败")
            return {}, {}

        canvas = self._layout.get_canvas()
        regions = self._layout.get_scene_regions(scene_key)
        if not regions:
            logger.warning(f"场景 {scene_key} 没有定义区域")
            return {}, {}

        if slot_keys:
            regions = [r for r in regions if r.key in slot_keys]

        # 建立 region_map（供 coord_meta 存储）
        region_map = {r.key: r for r in regions}

        # 逐 slot 裁切 + 识别
        result: dict[str, str] = {}
        h, w = img.shape[:2]
        canvas_x = canvas.x_ratio * w
        canvas_y = canvas.y_ratio * h
        canvas_w = canvas.w_ratio * w
        canvas_h = canvas.h_ratio * h

        for region in regions:
            x1 = int(canvas_x + region.x_ratio * canvas_w)
            y1 = int(canvas_y + region.y_ratio * canvas_h)
            x2 = int(canvas_x + (region.x_ratio + region.w_ratio) * canvas_w)
            y2 = int(canvas_y + (region.y_ratio + region.h_ratio) * canvas_h)
            slot_img = img[y1:y2, x1:x2]

            if slot_img.size == 0:
                logger.warning(f"slot {region.key} 裁切为空，跳过")
                result[region.key] = ""
                continue

            info = self.material_recognizer.recognize(slot_img, group=group)
            result[region.key] = info.type  # 空槽 info.type == ""
            logger.debug(
                f"材料识别 [{scene_key}].[{region.key}]: "
                f"type={info.type!r} level={info.level} count={info.count}"
            )

        fields_display = slot_keys if slot_keys else [r.key for r in self._layout.get_scene_regions(scene_key)]
        logger.info(f"材料识别 [{scene_key}]:{fields_display} => {result}")
        return result, region_map

    # ─── by 子句：短路识别 ──────────────────────────────────

    @staticmethod
    def _crop_region(img, region: Region, canvas: CanvasConfig) -> np.ndarray | None:
        """从大图中按 region 归一化坐标裁剪出小图"""
        h, w = img.shape[:2]
        cx = canvas.x_ratio * w
        cy = canvas.y_ratio * h
        cw = canvas.w_ratio * w
        ch = canvas.h_ratio * h
        x1 = int(cx + region.x_ratio * cw)
        y1 = int(cy + region.y_ratio * ch)
        x2 = int(cx + (region.x_ratio + region.w_ratio) * cw)
        y2 = int(cy + (region.y_ratio + region.h_ratio) * ch)
        crop = img[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        return crop

    @staticmethod
    def _validate_by_target(target_value, mode: str):
        """校验 by 子句 target 类型与 match_mode 是否匹配"""
        if mode in ("equals_any", "contains_any"):
            if not isinstance(target_value, list):
                raise ValueError(
                    f"by {mode} 要求 target 为 list 类型，"
                    f"实际为 {type(target_value).__name__}: {target_value!r}"
                )
        # equals / contains 接受 str（或可转 str 的值），无需严格校验

    @staticmethod
    def _match_text(text: str, target_value, mode: str) -> bool:
        """按 match_mode 判断 text 是否命中 target"""
        if mode == "equals":
            return text.strip() == str(target_value).strip()
        elif mode == "contains":
            return str(target_value) in text
        elif mode == "equals_any":
            stripped = text.strip()
            return any(stripped == str(v).strip() for v in target_value)
        elif mode == "contains_any":
            return any(str(v) in text for v in target_value)
        return False

    def ocr_scene_by(
        self,
        scene_key: str,
        field_keys: list[str],
        target_value,
        mode: str,
    ) -> str:
        """短路 OCR：一次截图，逐字段识别，首个命中即返回字段名

        Args:
            scene_key: 场景 key
            field_keys: 要识别的字段列表
            target_value: 匹配目标值（str 或 list，由 mode 决定）
            mode: equals | contains | equals_any | contains_any

        Returns:
            首个命中的 field_key（str），全部未命中返回 ""
        """
        self._validate_by_target(target_value, mode)

        img = self._capture.capture()
        if img is None:
            logger.error("截图失败")
            return ""

        canvas = self._layout.get_canvas()
        regions = self._layout.get_scene_regions(scene_key)
        if not regions:
            logger.warning(f"场景 {scene_key} 没有定义区域")
            return ""

        # 按 field_keys 顺序过滤并排序
        region_map = {r.key: r for r in regions}
        ordered_regions = [region_map[k] for k in field_keys if k in region_map]

        for region in ordered_regions:
            crop = self._crop_region(img, region, canvas)
            if crop is None:
                logger.debug(f"by OCR: region {region.key} 裁剪为空，跳过")
                continue
            ocr_results = self._ocr.recognize(crop)
            text = " | ".join(r.text for r in ocr_results) if ocr_results else ""
            if self._match_text(text, target_value, mode):
                logger.debug(f"by OCR 命中: [{scene_key}].[{region.key}] text={text!r} mode={mode}")
                return region.key

        logger.debug(f"by OCR 未命中: [{scene_key}]:{field_keys} mode={mode}")
        return ""

    def recognize_materials_by(
        self,
        scene_key: str,
        field_keys: list[str],
        target_value,
        mode: str,
        group: str | None = None,
    ) -> str:
        """短路材料识别：一次截图，逐 slot 识别，首个命中即返回 slot 名

        Args:
            scene_key: 场景 key
            field_keys: 要识别的 slot 列表
            target_value: 匹配目标值（str 或 list，由 mode 决定）
            mode: equals | contains | equals_any | contains_any
            group: 可选，限定材料分组范围

        Returns:
            首个命中的 slot_key（str），全部未命中返回 ""
        """
        self._validate_by_target(target_value, mode)

        img = self._capture.capture()
        if img is None:
            logger.error("截图失败")
            return ""

        canvas = self._layout.get_canvas()
        regions = self._layout.get_scene_regions(scene_key)
        if not regions:
            logger.warning(f"场景 {scene_key} 没有定义区域")
            return ""

        region_map = {r.key: r for r in regions}
        ordered_regions = [region_map[k] for k in field_keys if k in region_map]

        for region in ordered_regions:
            crop = self._crop_region(img, region, canvas)
            if crop is None:
                logger.debug(f"by 材料识别: region {region.key} 裁剪为空，跳过")
                continue
            info = self.material_recognizer.recognize(crop, group=group)
            if self._match_text(info.type, target_value, mode):
                logger.info(f"by 材料识别命中: [{scene_key}].[{region.key}] type={info.type!r} mode={mode} group={group}")
                return region.key

        logger.info(f"by 材料识别未命中: [{scene_key}]:{field_keys} mode={mode} group={group}")
        return ""

    # ─── 等待 ──────────────────────────────────────────────

    def wait_delay(self, delay_name: str):
        """按命名延迟参数等待（可被停止请求打断）"""
        delay_val = getattr(self._delay, delay_name, None)
        if delay_val is None:
            logger.error(f"未知的延迟参数: {delay_name}")
            return
        if isinstance(delay_val, tuple):
            actual = random.uniform(*delay_val)
        else:
            actual = float(delay_val)
        logger.debug(f"等待 {delay_name} = {actual:.2f}s")
        self.wait_seconds(actual)

    def wait_seconds(self, seconds: float):
        """固定等待（可被停止请求打断）

        用 50ms 轮询代替 time.sleep 阻塞，期间持续检查停止标志，
        使 F10 / 停止按钮能在等待期间立即生效，而不是等整段 sleep 结束。
        """
        logger.debug(f"等待 {seconds}s")
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline:
            if self._stop_check():
                logger.info("等待期间收到停止请求，提前结束")
                return
            remaining = deadline - time.monotonic()
            time.sleep(min(0.05, max(0.0, remaining)))

    # ─── 变量与函数调用 ────────────────────────────────────

    def get_variable(self, name: str):
        """获取变量值"""
        return self.variables.get(name)

    def set_variable(self, name: str, value):
        """设置变量"""
        self.variables[name] = value

    def call_function(self, func_name: str, args: list, engine=None) -> any:
        """调用内置函数

        若函数第一参数名为 _engine，自动注入当前 Engine 实例。
        若函数第一参数名为 _wf，自动注入 workflow 实例（兼容旧代码）。
        """
        fn = builtins.get_function(func_name)
        if fn is None:
            available = ", ".join(builtins.list_functions())
            raise ValueError(f"未知内置函数: {func_name}，可用函数: {available}")
        # 检查函数是否需要 engine 注入（第一参数名为 _engine）
        import inspect
        sig = inspect.signature(fn)
        params = list(sig.parameters.keys())
        if params and params[0] == '_engine' and engine is not None:
            return fn(engine, *args)
        if params and params[0] == '_wf':
            return fn(self, *args)
        return fn(*args)

    # ─── 坐标计算 ──────────────────────────────────────────

    def _region_to_screen(self, region: Region, jitter: bool = True) -> tuple[int | None, int | None]:
        """区域坐标 → 屏幕坐标"""
        w, h = self._capture.get_capture_size()
        canvas = self._layout.get_canvas()

        canvas_x = canvas.x_ratio * w
        canvas_y = canvas.y_ratio * h
        canvas_w = canvas.w_ratio * w
        canvas_h = canvas.h_ratio * h

        cx = canvas_x + (region.x_ratio + region.w_ratio / 2) * canvas_w
        cy = canvas_y + (region.y_ratio + region.h_ratio / 2) * canvas_h

        if jitter:
            jitter_ratio = self._delay.region_jitter_ratio
            region_w = region.w_ratio * canvas_w
            region_h = region.h_ratio * canvas_h
            cx += region_w * random.uniform(-jitter_ratio, jitter_ratio)
            cy += region_h * random.uniform(-jitter_ratio, jitter_ratio)

        return int(self._window_left + cx), int(self._window_top + cy)

    # ─── Point / Arrow 操作 ────────────────────────────────

    def click_any(self, scene_key: str, key: str):
        """点击 region 或 point（自动识别，region 优先）"""
        regions = self._layout.get_scene_regions(scene_key)
        region = next((r for r in regions if r.key == key), None)
        if region is not None:
            self.click_region(scene_key, key)
            return
        points = self._layout.get_scene_points(scene_key)
        point = next((p for p in points if p.key == key), None)
        if point is not None:
            self.click_point(scene_key, key)
            return
        logger.error(f"场景 {scene_key} 没有定义 region 或 point: {key}")

    def click_point(self, scene_key: str, point_key: str):
        """点击 point 中心（带半径内随机偏移）"""
        points = self._layout.get_scene_points(scene_key)
        point = next((p for p in points if p.key == point_key), None)
        if point is None:
            logger.error(f"场景 {scene_key} 没有定义 point: {point_key}")
            return
        screen_x, screen_y = self._point_to_screen(point)
        if screen_x is None:
            return
        logger.debug(f"点击 point: {scene_key}/{point_key} -> 屏幕({screen_x},{screen_y})")
        self._input.click_screen(screen_x, screen_y, f"{scene_key}/{point_key}")

    def drag_arrow(self, scene_key: str, arrow_key: str, duration: float | tuple[float, float] | None = None, hold: float | None = None):
        """执行 arrow 定义的拖拽

        Args:
            duration: 拖拽移动时长（秒）。单值固定，二元组则范围内随机。None 使用默认值。
            hold: 到达目标后按住不放的时长（秒）。None 表示不按住。
        """
        arrows = self._layout.get_scene_arrows(scene_key)
        arrow = next((a for a in arrows if a.key == arrow_key), None)
        if arrow is None:
            logger.error(f"场景 {scene_key} 没有定义 arrow: {arrow_key}")
            return
        points = self._layout.get_scene_points(scene_key)
        from_point = next((p for p in points if p.key == arrow.from_key), None)
        if from_point is None:
            logger.error(f"arrow {arrow_key} 的起点 point {arrow.from_key} 未定义")
            return
        # 终点：吸附态动态查 point，绝对态直接用坐标
        if arrow.to_key is not None:
            to_point = next((p for p in points if p.key == arrow.to_key), None)
            if to_point is None:
                logger.error(f"arrow {arrow_key} 的终点 point {arrow.to_key} 未定义")
                return
            to_cx, to_cy = to_point.cx_ratio, to_point.cy_ratio
        else:
            to_cx, to_cy = arrow.to_cx_ratio, arrow.to_cy_ratio
        fx, fy = self._point_to_screen(from_point)
        tx, ty = self._ratio_to_screen(to_cx, to_cy)
        if fx is None or tx is None:
            return
        logger.debug(f"拖拽 arrow: {scene_key}/{arrow_key} ({fx},{fy})->({tx},{ty})" + (f" hold {hold}s" if hold else ""))
        self._input.drag_screen(fx, fy, tx, ty, f"{scene_key}/{arrow_key}", duration=duration, hold=hold)

    def _point_to_screen(self, point: Point) -> tuple[int | None, int | None]:
        """point 中心 → 屏幕坐标（带半径内随机偏移）"""
        size = self._capture.get_capture_size()
        if size == (0, 0):
            logger.error("无法获取截屏尺寸")
            return None, None
        w, h = size
        canvas = self._layout.get_canvas()
        canvas_x = canvas.x_ratio * w
        canvas_y = canvas.y_ratio * h
        canvas_w = canvas.w_ratio * w
        canvas_h = canvas.h_ratio * h
        cx = canvas_x + point.cx_ratio * canvas_w
        cy = canvas_y + point.cy_ratio * canvas_h
        # 半径内随机偏移
        r = point.r_ratio * min(canvas_w, canvas_h)
        angle = random.uniform(0, 2 * math.pi)
        dist = random.uniform(0, r)
        cx += dist * math.cos(angle)
        cy += dist * math.sin(angle)
        return int(self._window_left + cx), int(self._window_top + cy)

    def _ratio_to_screen(self, cx_ratio: float, cy_ratio: float) -> tuple[int | None, int | None]:
        """画布内归一化坐标 → 屏幕坐标"""
        size = self._capture.get_capture_size()
        if size == (0, 0):
            logger.error("无法获取截屏尺寸")
            return None, None
        w, h = size
        canvas = self._layout.get_canvas()
        sx = canvas.x_ratio + cx_ratio * canvas.w_ratio
        sy = canvas.y_ratio + cy_ratio * canvas.h_ratio
        return int(self._window_left + sx * w), int(self._window_top + sy * h)

    # ─── Panel 操作原语 ─────────────────────────────────────

    def _find_panel(self, scene_key: str, panel_key: str):
        """在 layout 中查找 panel 实例"""
        panels = self._layout.get_scene_panels(scene_key)
        return next((p for p in panels if p.key == panel_key), None)

    def _capture_panel_image(self, panel_obj) -> "np.ndarray | None":
        """截取 panel 区域图像"""
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
        px = canvas_x + int(panel_obj.x_ratio * canvas_w)
        py = canvas_y + int(panel_obj.y_ratio * canvas_h)
        pw = max(1, int(panel_obj.w_ratio * canvas_w))
        ph = max(1, int(panel_obj.h_ratio * canvas_h))
        h_img, w_img = full.shape[:2]
        x1, y1 = max(0, min(px, w_img)), max(0, min(py, h_img))
        x2, y2 = max(0, min(px + pw, w_img)), max(0, min(py + ph, h_img))
        if x2 <= x1 or y2 <= y1:
            return None
        return full[y1:y2, x1:x2].copy()

    def _panel_ratio_to_screen(self, panel_obj, cx: float, cy: float) -> tuple[int, int]:
        """panel 内归一化坐标 → 屏幕绝对坐标"""
        w, h = self._capture.get_capture_size()
        canvas = self._layout.get_canvas()
        canvas_x = canvas.x_ratio * w
        canvas_y = canvas.y_ratio * h
        canvas_w = canvas.w_ratio * w
        canvas_h = canvas.h_ratio * h
        sx = canvas_x + (panel_obj.x_ratio + cx * panel_obj.w_ratio) * canvas_w
        sy = canvas_y + (panel_obj.y_ratio + cy * panel_obj.h_ratio) * canvas_h
        return int(self._window_left + sx), int(self._window_top + sy)

    def align_panel(self, scene_key: str, panel_key: str) -> "GridAlignment | None":
        """对齐 panel 网格，缓存结果并返回 GridAlignment"""
        panel_obj = self._find_panel(scene_key, panel_key)
        if panel_obj is None:
            logger.error(f"align: 场景 {scene_key} 未找到 panel {panel_key}")
            return None
        panel_img = self._capture_panel_image(panel_obj)
        if panel_img is None:
            logger.error(f"align: 无法截取 panel {scene_key}.{panel_key}")
            return None
        alignment = detect_grid(panel_img, expected_rows=panel_obj.rows, expected_cols=panel_obj.cols)
        if alignment is None:
            logger.error(f"align: panel {scene_key}.{panel_key} 未检测到 slot")
            return None
        self._panel_alignments[(scene_key, panel_key)] = alignment
        logger.info(f"align: {scene_key}.{panel_key} 已对齐，检测到 {alignment.total_slots} 个 slot 中心")
        return alignment

    def _ensure_aligned(self, scene_key: str, panel_key: str) -> "GridAlignment | None":
        """确保 panel 已对齐，未对齐则自动触发"""
        cache_key = (scene_key, panel_key)
        if cache_key not in self._panel_alignments:
            return self.align_panel(scene_key, panel_key)
        return self._panel_alignments[cache_key]

    def _invalidate_align(self, scene_key: str, panel_key: str):
        """失效 panel 对齐缓存（drag 后调用）"""
        self._panel_alignments.pop((scene_key, panel_key), None)

    def click_panel(self, scene_key: str, panel_key: str, row: int, col: int) -> bool:
        """点击 panel 中指定格子（1-based），返回是否成功"""
        cal = self._ensure_aligned(scene_key, panel_key)
        if cal is None:
            return False
        row_idx, col_idx = row - 1, col - 1
        if not (0 <= row_idx < cal.n_rows and 0 <= col_idx < cal.n_cols):
            logger.debug(f"panel 索引越界: [{row}][{col}]，对齐结果 {cal.n_rows}×{cal.n_cols}")
            return False
        panel_obj = self._find_panel(scene_key, panel_key)
        if panel_obj is None:
            return False
        cx, cy = cal.slot_center(row_idx, col_idx)
        sx, sy = self._panel_ratio_to_screen(panel_obj, cx, cy)
        self._input.click_screen(sx, sy, f"panel({scene_key}.{panel_key}[{row}][{col}])")
        return True

    def scan_panel(self, scene_key: str, panel_key: str, row: int, col: int,
                   detail_scene: str, fields: list[str]) -> dict:
        """点击格子后 OCR 详情场景，返回解析后的装备 dict（空则返回 {}）"""
        if not self.click_panel(scene_key, panel_key, row, col):
            return {}
        raw = self.ocr_scene(detail_scene, fields)
        if not raw:
            return {}
        return self.call_function("to_equipment", [raw])

    def drag_grid(self, scene_key: str, panel_key: str, direction: str,
                  distance: float = 1.0, hold: float | None = None):
        """按 panel 中心执行 grid 级拖拽（滚动），drag 后自动失效缓存"""
        panel_obj = self._find_panel(scene_key, panel_key)
        if panel_obj is None:
            logger.error(f"drag grid: 未找到 panel {scene_key}.{panel_key}")
            return
        cal = self._ensure_aligned(scene_key, panel_key)
        if cal is None or cal.row_slot <= 0:
            logger.error(f"drag grid: align 失败: {scene_key}.{panel_key}")
            return
        # 起点：panel 中心
        cx = panel_obj.x_ratio + panel_obj.w_ratio / 2
        cy = panel_obj.y_ratio + panel_obj.h_ratio / 2
        w, h = self._capture.get_capture_size()
        canvas = self._layout.get_canvas()
        x = int((canvas.x_ratio + cx * canvas.w_ratio) * w + self._window_left)
        y = int((canvas.y_ratio + cy * canvas.h_ratio) * h + self._window_top)
        # 距离计算
        dx, dy = 0, 0
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
                return
            step_norm = cal.col_slot + cal.col_span / 2.0
            panel_pixel_w = panel_obj.w_ratio * canvas.w_ratio * w
            dx = int(step_norm * panel_pixel_w * distance)
            if direction == "left":
                dx = -dx
            if abs(dx) < 10:
                dx = 10 if dx >= 0 else -10
        self._input.drag_screen(
            x, y, x + dx, y + dy,
            f"grid({scene_key}.{panel_key}) {direction} {distance}",
            hold=hold,
        )
        # drag 后失效缓存，下次访问时懒加载重新对齐
        self._invalidate_align(scene_key, panel_key)

    def get_panel_rows(self, scene_key: str, panel_key: str) -> int:
        """返回 panel 实际检测到的行数"""
        cal = self._ensure_aligned(scene_key, panel_key)
        return cal.n_rows if cal else 0

    def get_panel_cols(self, scene_key: str, panel_key: str) -> int:
        """返回 panel 实际检测到的列数"""
        cal = self._ensure_aligned(scene_key, panel_key)
        return cal.n_cols if cal else 0
