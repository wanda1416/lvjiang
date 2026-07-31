"""Panel 对齐与 cell 级操作 Mixin

panel 没在布局里定义、行列索引不是数值都是脚本 / 布局配错，抛错中断；
未对齐与索引越界属于运行时状态，仍返回空值——脚本把越界当作遍历
网格的终止条件。
"""

import numpy as np
from loguru import logger

from ..align import detect_grid
from ..grammar import (
    ByClause,
    PanelRef,
    Recognize,
    Scan,
    VarRef,
)
from ..grammar.ast_nodes import Align
from .signals import WorkflowUserError


class _PanelMixin:
    """Panel 对齐与路由：align / panel cell 裁剪与识别 / 坐标换算"""

    def _exec_align(self, node: "Align"):
        """align [scene].[panel] — 截图 panel 区域 + 运行图像自对齐，缓存 slot 中心"""
        scene_key = node.scene
        panel_key = node.panel
        panel_obj = self._find_panel_in_layout(scene_key, panel_key)
        if panel_obj is None:
            raise WorkflowUserError(
                f"align: 布局中未定义 panel {scene_key}.{panel_key}，"
                f"请在场景布局编辑器中绑定后重试"
            )
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
            raise WorkflowUserError(f"panel 索引非数值: row={row}, col={col}") from None

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
            raise WorkflowUserError(
                f"布局中未定义 panel {scene_key}.{panel_key}，"
                f"请在场景布局编辑器中绑定后重试"
            )

        # 查表：用对齐结果的行列数做越界检查
        if not (0 <= row_idx < cal.n_rows and 0 <= col_idx < cal.n_cols):
            logger.debug(f"panel 索引越界: [{row_idx + 1}][{col_idx + 1}]，"
                         f"对齐结果 {cal.n_rows}×{cal.n_cols}")
            return None, None

        cx, cy = cal.slot_center(row_idx, col_idx)  # 相对于 panel 区域
        return self._panel_ratio_to_screen(panel_obj, cx, cy)

    def _crop_slot_image(self, ref: PanelRef) -> "tuple[np.ndarray | None, str, int, int]":
        """裁剪 panel cell 图像，返回 (slot_img, slot_key, row_idx, col_idx)

        slot_key 格式: "r{row}c{col}"（1-based），仅用于日志定位。
        """
        scene_key = ref.scene
        panel_key = ref.panel
        row = self._resolve(ref.row) if isinstance(ref.row, VarRef) else ref.row
        col = self._resolve(ref.col) if isinstance(ref.col, VarRef) else ref.col
        try:
            row_idx = int(float(row)) - 1
            col_idx = int(float(col)) - 1
        except (TypeError, ValueError):
            raise WorkflowUserError(f"panel 索引非数值: row={row}, col={col}") from None

        # 自动 align
        cache_key = (scene_key, panel_key)
        if cache_key not in self._panel_alignments:
            auto_node = Align(scene=scene_key, panel=panel_key)
            self._exec_align(auto_node)
        cal = self._panel_alignments.get(cache_key)
        panel_obj = self._find_panel_in_layout(scene_key, panel_key)
        if panel_obj is None:
            raise WorkflowUserError(
                f"布局中未定义 panel {scene_key}.{panel_key}，"
                f"请在场景布局编辑器中绑定后重试"
            )
        if cal is None:
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
        """scan [scene].[panel][row][col] as $var [by ...]

        [row][col] 是对整面板结果的 key 过滤，结果为该格文本（str），
        与整面板 $var.[行].[列] 取值格式一致。
        """
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
            self.variables[var_name] = text
        logger.info(f"scan panel cell [{ref.scene}.{ref.panel}][{slot_key}] => {self.variables[var_name]}")

    def _recognize_panel_cell(self, node: Recognize):
        """recognize [scene].[panel][row][col] as $var [by ...] [on group ...]

        [row][col] 是 key 过滤，结果为该格材料类型名（str）。
        """
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
            self.variables[var_name] = info.type
        logger.info(f"recognize panel cell [{ref.scene}.{ref.panel}][{slot_key}] => {self.variables[var_name]}")

    def _aligned_panel_image(self, scene_key: str, panel_key: str):
        """自动 align 并截取 panel 全图，返回 (panel_img, cal)；失败 (None, None)"""
        cache_key = (scene_key, panel_key)
        if cache_key not in self._panel_alignments:
            self._exec_align(Align(scene=scene_key, panel=panel_key))
        cal = self._panel_alignments.get(cache_key)
        if cal is None:
            logger.error(f"panel 未对齐: {scene_key}.{panel_key}")
            return None, None
        panel_obj = self._find_panel_in_layout(scene_key, panel_key)
        if panel_obj is None:
            raise WorkflowUserError(
                f"布局中未定义 panel {scene_key}.{panel_key}，"
                f"请在场景布局编辑器中绑定后重试"
            )
        panel_img = self._capture_panel_image(panel_obj)
        if panel_img is None:
            logger.error(f"无法截取 panel {scene_key}.{panel_key}")
            return None, None
        return panel_img, cal

    def _iter_slot_images(self, panel_img, cal):
        """按对齐结果逐格裁剪 panel 图像，yield (row_idx, col_idx, slot_img|None)"""
        ph, pw = panel_img.shape[:2]
        for r in range(cal.n_rows):
            for c in range(cal.n_cols):
                x1_r, y1_r, x2_r, y2_r = cal.slot_bounds(r, c)
                x1 = max(0, int(x1_r * pw))
                y1 = max(0, int(y1_r * ph))
                x2 = min(pw, int(x2_r * pw))
                y2 = min(ph, int(y2_r * ph))
                slot_img = panel_img[y1:y2, x1:x2]
                yield r, c, (slot_img if slot_img.size else None)

    def _scan_panel_whole(self, scene_key: str, panel_key: str, var_name: str):
        """scan [scene].[panel] as $var — 整面板逐格 OCR

        结果为行列嵌套 dict（key 为 1-based 字符串）：$var.[1].[2] 取 1 行 2 列文本。
        整面板只截一次图，所有格从同一帧裁剪，避免逐格重截的耗时与画面漂移。
        """
        panel_img, cal = self._aligned_panel_image(scene_key, panel_key)
        if panel_img is None:
            self.variables[var_name] = {}
            return
        result: dict[str, dict[str, str]] = {}
        for r, c, slot_img in self._iter_slot_images(panel_img, cal):
            text = ""
            if slot_img is not None:
                ocr_results = self._ocr.recognize(slot_img)
                text = " ".join(t.text for t in ocr_results).strip()
            result.setdefault(str(r + 1), {})[str(c + 1)] = text
        self.variables[var_name] = result
        logger.info(f"scan panel [{scene_key}.{panel_key}] {cal.n_rows}×{cal.n_cols} => {result}")

    def _recognize_panel_whole(self, scene_key: str, panel_key: str, var_name: str, group=None):
        """recognize [scene].[panel] as $var [on group ...] — 整面板逐格材料识别

        结果结构与 _scan_panel_whole 一致：$var.[行].[列] 取材料类型名。
        """
        panel_img, cal = self._aligned_panel_image(scene_key, panel_key)
        if panel_img is None:
            self.variables[var_name] = {}
            return
        recognizer = self._ensure_workflow().material_recognizer
        result: dict[str, dict[str, str]] = {}
        for r, c, slot_img in self._iter_slot_images(panel_img, cal):
            mat_type = ""
            if slot_img is not None:
                mat_type = recognizer.recognize(slot_img, group=group).type
            result.setdefault(str(r + 1), {})[str(c + 1)] = mat_type
        self.variables[var_name] = result
        logger.info(f"recognize panel [{scene_key}.{panel_key}] {cal.n_rows}×{cal.n_cols} => {result}")

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
        """panel 内归一化坐标 → 屏幕绝对坐标（钳位到 panel 内缩边距内）

        与 WorkflowBase._panel_ratio_to_screen 同步：半可见行的 slot 中心
        可能压在 panel 边缘，叠加 ±click_random_offset 随机偏移后 tap 会
        落到网格黑框之外，钳位到内缩 margin 矩形内保证点中可见部分。
        """
        w, h = self._capture.get_capture_size()
        canvas = self._layout.get_canvas()
        canvas_x = canvas.x_ratio * w
        canvas_y = canvas.y_ratio * h
        canvas_w = canvas.w_ratio * w
        canvas_h = canvas.h_ratio * h
        px = canvas_x + panel_obj.x_ratio * canvas_w
        py = canvas_y + panel_obj.y_ratio * canvas_h
        pw = panel_obj.w_ratio * canvas_w
        ph = panel_obj.h_ratio * canvas_h
        sx = px + cx_panel * pw
        sy = py + cy_panel * ph
        margin = self._input_sim.click_random_offset + 2
        csx = max(px + margin, min(sx, px + pw - margin))
        csy = max(py + margin, min(sy, py + ph - margin))
        if (csx, csy) != (sx, sy):
            logger.debug(f"panel 坐标钳位: ({sx:.0f},{sy:.0f}) → ({csx:.0f},{csy:.0f})")
        return int(self._window_left + csx), int(self._window_top + csy)
