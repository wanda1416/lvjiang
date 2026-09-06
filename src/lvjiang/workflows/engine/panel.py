"""Panel 对齐与 cell 级操作 Mixin

panel 没在布局里定义、行列索引不是数值都是脚本 / 布局配错，抛错中断；
未对齐与索引越界属于运行时状态，仍返回空值——脚本把越界当作遍历
网格的终止条件。
"""

import numpy as np
from loguru import logger

from ...i18n import tr
from ..align import GridAlignment, _make_even_alignment, detect_grid
from ..grammar import (
    ByClause,
    PanelRef,
    Recognize,
    Scan,
    VarRef,
)
from ..grammar.ast_nodes import Align
from ..runtime_layout import require_enabled
from .signals import WorkflowUserError


class _PanelMixin:
    """Panel 对齐与路由：align / panel cell 裁剪与识别 / 坐标换算"""

    def _exec_align(self, node: "Align"):
        """align [scene].[panel] — 截图 panel 区域 + 运行图像自对齐，缓存 slot 中心

        校准模式由 panel.calibration 控制：
        - "even"：跳过图像检测，直接按 rows/cols 等分
        - "image"：仅图像检测，失败返回 None
        - "auto"（默认）：先图像检测，失败降级为等分
        """
        scene_key = node.scene
        panel_key = node.panel
        panel_obj = self._find_panel_in_layout(scene_key, panel_key)
        if panel_obj is None:
            raise WorkflowUserError(
                f"align: 布局中未定义 panel {scene_key}.{panel_key}，"
                f"请在场景布局编辑器中绑定后重试"
            )
        calibration = getattr(panel_obj, "calibration", "auto")

        # even 模式：跳过图像检测，直接等分
        alignment: GridAlignment | None
        if calibration == "even":
            alignment = _make_even_alignment(panel_obj.rows, panel_obj.cols)
            self._panel_alignments[(scene_key, panel_key)] = alignment
            logger.info(
                f"align: {scene_key}.{panel_key} 等分模式，"
                f"{alignment.n_rows}×{alignment.n_cols} = {alignment.total_slots} 个 slot"
            )
            return

        # image / auto 模式：先尝试图像检测
        panel_img = self._capture_panel_image(panel_obj)
        if panel_img is None:
            logger.error(f"align: 无法截取 panel {scene_key}.{panel_key}")
            if calibration == "auto":
                alignment = _make_even_alignment(panel_obj.rows, panel_obj.cols)
                self._panel_alignments[(scene_key, panel_key)] = alignment
                logger.warning(f"align: {scene_key}.{panel_key} 截图失败，降级为等分模式")
            return

        fallback = (calibration == "auto")
        alignment = detect_grid(
            panel_img,
            expected_rows=panel_obj.rows,
            expected_cols=panel_obj.cols,
            fallback=fallback,
            scroll_direction=getattr(panel_obj, "scroll_direction", "vertical"),
        )
        if alignment is None:
            logger.error(f"align: panel {scene_key}.{panel_key} 未检测到 slot")
            return
        self._panel_alignments[(scene_key, panel_key)] = alignment
        mode = tr("等分降级") if (fallback and alignment.row_span == 0.0) else tr("图像检测")
        logger.info(
            f"align: {scene_key}.{panel_key} 已对齐（{mode}），"
            f"检测到 {alignment.n_rows}×{alignment.n_cols} = {alignment.total_slots} 个 slot 中心"
        )

    def _panel_ref_to_screen(self, ref: PanelRef) -> tuple[int | None, int | None]:
        """PanelRef → 屏幕绝对坐标

        若缓存未命中，自动触发一次 align。
        """
        # scene / panel 支持静态字符串或 $var（与 EntityRef 动态引用语义一致）
        scene_key = self._resolve(ref.scene) if isinstance(ref.scene, VarRef) else ref.scene
        panel_key = self._resolve(ref.panel) if isinstance(ref.panel, VarRef) else ref.panel
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
        # scene / panel 支持静态字符串或 $var（与 EntityRef 动态引用语义一致）
        scene_key = self._resolve(ref.scene) if isinstance(ref.scene, VarRef) else ref.scene
        panel_key = self._resolve(ref.panel) if isinstance(ref.panel, VarRef) else ref.panel
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
        """scan [scene].[panel][row][col] as $var [by ...] [where ...]

        [row][col] 是对整面板结果的 key 过滤，结果为该格文本（str），
        与整面板 $var.[行].[列] 取值格式一致。
        """
        ref: PanelRef = node.scene
        var_name = node.target.name if isinstance(node.target, VarRef) else str(node.target)
        slot_img, slot_key, _, _ = self._crop_slot_image(ref)
        if slot_img is None:
            self.variables[var_name] = ""
            return
        min_conf = self._resolve_min_confidence(node.where)
        if node.by is not None:
            # by 子句：短路匹配，返回是否命中
            by_clause: ByClause = node.by
            target_value = self._resolve(by_clause.target)
            ocr_results = self._ocr.recognize(slot_img)
            if min_conf is not None:
                ocr_results = [r for r in ocr_results if r.confidence >= min_conf]
            text = " ".join(r.text for r in ocr_results).strip()
            matched = self._match_text(text, target_value, by_clause.match_mode)
            self.variables[var_name] = text if matched else ""
        else:
            ocr_results = self._ocr.recognize(slot_img)
            if min_conf is not None:
                ocr_results = [r for r in ocr_results if r.confidence >= min_conf]
            text = " ".join(r.text for r in ocr_results).strip()
            self.variables[var_name] = text
        logger.info(f"scan panel cell [{ref.scene}.{ref.panel}][{slot_key}] => {self.variables[var_name]}")

    def _scan_panel_range(self, node: Scan):
        """scan [scene].[panel][r1...r2][c1...c2] as $var [by ...] [where ...] — 面板范围 OCR

        仅扫描指定行列范围，结果结构与整面板一致：$var.[行].[列]。
        """
        ref: PanelRef = node.scene
        var_name = node.target.name if isinstance(node.target, VarRef) else str(node.target)
        scene_key = self._resolve(ref.scene) if isinstance(ref.scene, VarRef) else ref.scene
        panel_key = self._resolve(ref.panel) if isinstance(ref.panel, VarRef) else ref.panel

        # 解析行范围
        if isinstance(ref.row, tuple):
            row_start = self._resolve_range_endpoint(ref.row[0])
            row_end = self._resolve_range_endpoint(ref.row[1])
        else:
            r = int(self._resolve(ref.row)) if isinstance(ref.row, VarRef) else int(ref.row)
            row_start = row_end = r

        # 解析列范围
        if isinstance(ref.col, tuple):
            col_start = self._resolve_range_endpoint(ref.col[0])
            col_end = self._resolve_range_endpoint(ref.col[1])
        else:
            c = int(self._resolve(ref.col)) if isinstance(ref.col, VarRef) else int(ref.col)
            col_start = col_end = c

        panel_img, cal = self._aligned_panel_image(scene_key, panel_key)
        if panel_img is None:
            self.variables[var_name] = {}
            return

        min_conf = self._resolve_min_confidence(node.where)
        result: dict[str, dict[str, str]] = {}
        for r_1based in range(row_start, row_end + 1):
            for c_1based in range(col_start, col_end + 1):
                r_idx, c_idx = r_1based - 1, c_1based - 1
                if not (0 <= r_idx < cal.n_rows and 0 <= c_idx < cal.n_cols):
                    result.setdefault(str(r_1based), {})[str(c_1based)] = ""
                    continue
                slot_img = cal.crop_slot(panel_img, r_idx, c_idx)
                if slot_img is None:
                    result.setdefault(str(r_1based), {})[str(c_1based)] = ""
                    continue
                ocr_results = self._ocr.recognize(slot_img)
                if min_conf is not None:
                    ocr_results = [r for r in ocr_results if r.confidence >= min_conf]
                text = " ".join(t.text for t in ocr_results).strip()
                result.setdefault(str(r_1based), {})[str(c_1based)] = text

        self.variables[var_name] = result
        logger.info(f"scan panel range [{scene_key}.{panel_key}][{row_start}...{row_end}][{col_start}...{col_end}] => {result}")

    def _recognize_panel_cell(self, node: Recognize):
        """recognize [scene].[panel][row][col] as [rich] $var [by ...] [on group ...] [where ...]

        [row][col] 是 key 过滤，结果为该格参考标签（str）或富 dict（rich 模式）。
        """
        ref: PanelRef = node.scene
        var_name = node.target.name if isinstance(node.target, VarRef) else str(node.target)
        slot_img, slot_key, _, _ = self._crop_slot_image(ref)
        if slot_img is None:
            self.variables[var_name] = {} if node.rich else ""
            return
        group = self._resolve(node.group) if node.group is not None else None
        min_conf = self._resolve_min_confidence(node.where)
        recognizer = self._ensure_workflow().reference_recognizer
        if node.by is not None:
            # by 优先：rich 不影响短路匹配语义
            by_clause: ByClause = node.by
            target_value = self._resolve(by_clause.target)
            info = recognizer.recognize_only(slot_img, group=group)
            if min_conf is not None and info.confidence < min_conf:
                self.variables[var_name] = ""
            else:
                matched = self._match_text(info.label, target_value, by_clause.match_mode)
                self.variables[var_name] = info.label if matched else ""
        elif node.rich:
            # rich 模式：返回富 dict
            info = recognizer.recognize(slot_img, group=group)
            if min_conf is not None and info.confidence < min_conf:
                self.variables[var_name] = {}
            elif not info.label:
                self.variables[var_name] = {}
            else:
                base = recognizer.build_rich_base(info)
                if node.with_func is not None:
                    from .. import builtins
                    func_name = node.with_func.value if hasattr(node.with_func, 'value') else str(node.with_func)
                    transform = builtins.get_function(func_name)
                    if transform is None:
                        raise ValueError(f"未知内置函数: {func_name}")
                    base = transform(base)
                self.variables[var_name] = base
        else:
            info = recognizer.recognize_only(slot_img, group=group)
            if min_conf is not None and info.confidence < min_conf:
                self.variables[var_name] = ""
            else:
                self.variables[var_name] = info.label
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

    def _scan_panel_whole(self, scene_key: str, panel_key: str, var_name: str, min_confidence: float | None = None):
        """scan [scene].[panel] as $var [where ...] — 整面板逐格 OCR

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
                if min_confidence is not None:
                    ocr_results = [r for r in ocr_results if r.confidence >= min_confidence]
                text = " ".join(t.text for t in ocr_results).strip()
            result.setdefault(str(r + 1), {})[str(c + 1)] = text
        self.variables[var_name] = result
        logger.info(f"scan panel [{scene_key}.{panel_key}] {cal.n_rows}×{cal.n_cols} => {result}")

    def _recognize_panel_whole(self, scene_key: str, panel_key: str, var_name: str, group=None, min_confidence: float | None = None, rich: bool = False, with_func=None):
        """recognize [scene].[panel] as [rich] $var [...] — 整面板逐格参考图识别

        结果结构与 _scan_panel_whole 一致：$var.[行].[列] 取参考标签或富 dict（rich 模式）。
        """
        panel_img, cal = self._aligned_panel_image(scene_key, panel_key)
        if panel_img is None:
            self.variables[var_name] = {}
            return
        recognizer = self._ensure_workflow().reference_recognizer
        # 解析 with 转换函数
        transform = None
        if rich and with_func is not None:
            from .. import builtins
            func_name = with_func.value if hasattr(with_func, 'value') else str(with_func)
            transform = builtins.get_function(func_name)
            if transform is None:
                raise ValueError(f"未知内置函数: {func_name}")
        result: dict[str, dict[str, str | dict]] = {}
        for r, c, slot_img in self._iter_slot_images(panel_img, cal):
            if slot_img is None:
                cell_value: str | dict = {} if rich else ""
            else:
                info = (
                    recognizer.recognize(slot_img, group=group)
                    if rich
                    else recognizer.recognize_only(slot_img, group=group)
                )
                if min_confidence is not None and info.confidence < min_confidence:
                    cell_value = {} if rich else ""
                elif rich:
                    if info.label:
                        base = recognizer.build_rich_base(info)
                        cell_value = transform(base) if transform is not None else base
                    else:
                        cell_value = {}
                else:
                    cell_value = info.label
            result.setdefault(str(r + 1), {})[str(c + 1)] = cell_value
        self.variables[var_name] = result
        logger.info(f"recognize panel [{scene_key}.{panel_key}] {cal.n_rows}×{cal.n_cols} => {result}")

    def _resolve_range_endpoint(self, val) -> int:
        """解析范围端点：int 直接返回，VarRef 查变量表"""
        if isinstance(val, VarRef):
            return int(self._resolve(val))
        return int(val)

    def _recognize_panel_range(self, node: Recognize):
        """recognize [scene].[panel][r1...r2][c1...c2] as [rich] $var [by ...] — 面板范围识别

        row/col 支持范围索引，仅识别指定行列子集。
        有 by 时降级返回位置 dict {"row": r, "col": c}，无 by 时返回完整 dict。
        """
        ref: PanelRef = node.scene
        var_name = node.target.name if isinstance(node.target, VarRef) else str(node.target)
        scene_key = self._resolve(ref.scene) if isinstance(ref.scene, VarRef) else ref.scene
        panel_key = self._resolve(ref.panel) if isinstance(ref.panel, VarRef) else ref.panel

        # 解析行范围
        if isinstance(ref.row, tuple):
            row_start = self._resolve_range_endpoint(ref.row[0])
            row_end = self._resolve_range_endpoint(ref.row[1])
        else:
            r = int(self._resolve(ref.row)) if isinstance(ref.row, VarRef) else int(ref.row)
            row_start = row_end = r

        # 解析列范围
        if isinstance(ref.col, tuple):
            col_start = self._resolve_range_endpoint(ref.col[0])
            col_end = self._resolve_range_endpoint(ref.col[1])
        else:
            c = int(self._resolve(ref.col)) if isinstance(ref.col, VarRef) else int(ref.col)
            col_start = col_end = c

        panel_img, cal = self._aligned_panel_image(scene_key, panel_key)
        if panel_img is None:
            self.variables[var_name] = {}
            return

        recognizer = self._ensure_workflow().reference_recognizer
        group = self._resolve(node.group) if node.group is not None else None
        min_conf = self._resolve_min_confidence(node.where)
        ph, pw = panel_img.shape[:2]

        # by 子句处理：降级返回位置 dict
        if node.by is not None:
            by_clause = node.by
            target_value = self._resolve(by_clause.target)
            match_mode = by_clause.match_mode
            full = by_clause.full

            best_pos = None
            best_confidence = -1.0

            for r_1based in range(row_start, row_end + 1):
                for c_1based in range(col_start, col_end + 1):
                    r_idx, c_idx = r_1based - 1, c_1based - 1
                    if not (0 <= r_idx < cal.n_rows and 0 <= c_idx < cal.n_cols):
                        continue
                    x1_r, y1_r, x2_r, y2_r = cal.slot_bounds(r_idx, c_idx)
                    x1 = max(0, int(x1_r * pw))
                    y1 = max(0, int(y1_r * ph))
                    x2 = min(pw, int(x2_r * pw))
                    y2 = min(ph, int(y2_r * ph))
                    slot_img = panel_img[y1:y2, x1:x2]
                    if slot_img.size == 0:
                        continue

                    info = recognizer.recognize_only(slot_img, group=group)
                    if min_conf is not None and info.confidence < min_conf:
                        continue
                    if self._match_text(info.label, target_value, match_mode):
                        if full:
                            confidence = getattr(info, 'confidence', 0.0)
                            if confidence > best_confidence:
                                best_pos = {"row": r_1based, "col": c_1based}
                                best_confidence = confidence
                        else:
                            self.variables[var_name] = {"row": r_1based, "col": c_1based}
                            logger.info(f"recognize panel range by [{scene_key}.{panel_key}] matched at row={r_1based}, col={c_1based}: {info.label!r}")
                            return

            if full and best_pos is not None:
                self.variables[var_name] = best_pos
                logger.info(f"recognize panel range full by [{scene_key}.{panel_key}] matched at row={best_pos['row']}, col={best_pos['col']} confidence={best_confidence:.3f}")
                return

            self.variables[var_name] = {}
            logger.info(f"recognize panel range by [{scene_key}.{panel_key}] no match")
            return

        # 无 by 子句：返回完整 dict
        transform = None
        if node.rich and node.with_func is not None:
            from .. import builtins
            func_name = node.with_func.value if hasattr(node.with_func, 'value') else str(node.with_func)
            transform = builtins.get_function(func_name)
            if transform is None:
                raise ValueError(f"未知内置函数: {func_name}")

        result: dict[str, dict[str, str | dict]] = {}
        for r_1based in range(row_start, row_end + 1):
            for c_1based in range(col_start, col_end + 1):
                r_idx, c_idx = r_1based - 1, c_1based - 1
                if not (0 <= r_idx < cal.n_rows and 0 <= c_idx < cal.n_cols):
                    continue
                x1_r, y1_r, x2_r, y2_r = cal.slot_bounds(r_idx, c_idx)
                x1 = max(0, int(x1_r * pw))
                y1 = max(0, int(y1_r * ph))
                x2 = min(pw, int(x2_r * pw))
                y2 = min(ph, int(y2_r * ph))
                slot_img = panel_img[y1:y2, x1:x2]
                if slot_img.size == 0:
                    continue
                slot_img = slot_img if slot_img.size else None

                if slot_img is None:
                    cell_value: str | dict = {} if node.rich else ""
                else:
                    info = (
                        recognizer.recognize(slot_img, group=group)
                        if node.rich
                        else recognizer.recognize_only(slot_img, group=group)
                    )
                    if min_conf is not None and info.confidence < min_conf:
                        cell_value = {} if node.rich else ""
                    elif node.rich:
                        if info.label:
                            base = recognizer.build_rich_base(info)
                            cell_value = transform(base) if transform is not None else base
                        else:
                            cell_value = {}
                    else:
                        cell_value = info.label
                result.setdefault(str(r_1based), {})[str(c_1based)] = cell_value

        self.variables[var_name] = result
        logger.info(
            f"recognize panel range [{scene_key}.{panel_key}]"
            f"[{row_start}...{row_end}][{col_start}...{col_end}] => {result}"
        )

    def _scan_panel_by(self, scene_key: str, panel_key: str, var_name: str, by_clause, group=None, min_confidence: float | None = None):
        """scan [scene].[panel] as $var by ... [where ...] — 整面板 OCR + by 短路匹配

        返回首个命中的行列位置 {"row": 行号, "col": 列号}，未命中返回空 dict {}。
        行列号为 1-based 整数。
        """
        panel_img, cal = self._aligned_panel_image(scene_key, panel_key)
        if panel_img is None:
            self.variables[var_name] = {}
            return
        target_value = self._resolve(by_clause.target)
        match_mode = by_clause.match_mode
        for r, c, slot_img in self._iter_slot_images(panel_img, cal):
            text = ""
            if slot_img is not None:
                ocr_results = self._ocr.recognize(slot_img)
                if min_confidence is not None:
                    ocr_results = [o for o in ocr_results if o.confidence >= min_confidence]
                text = " ".join(t.text for t in ocr_results).strip()
            if self._match_text(text, target_value, match_mode):
                self.variables[var_name] = {"row": r + 1, "col": c + 1}
                logger.info(f"scan panel by [{scene_key}.{panel_key}] matched at row={r+1}, col={c+1}: {text!r}")
                return
        self.variables[var_name] = {}
        logger.info(f"scan panel by [{scene_key}.{panel_key}] no match")

    def _recognize_panel_by(self, scene_key: str, panel_key: str, var_name: str, by_clause, group=None, min_confidence: float | None = None):
        """recognize [scene].[panel] as $var [full] by ... — 参考图识别 + by 匹配

        by_clause.full=False: 短路匹配，返回首个命中的行列位置 {"row": 行号, "col": 列号}
        by_clause.full=True: 全量匹配，返回置信度最高的行列位置
        未命中返回空 dict {}。行列号为 1-based 整数。
        """
        panel_img, cal = self._aligned_panel_image(scene_key, panel_key)
        if panel_img is None:
            self.variables[var_name] = {}
            return
        recognizer = self._ensure_workflow().reference_recognizer
        target_value = self._resolve(by_clause.target)
        match_mode = by_clause.match_mode
        full = by_clause.full

        best_pos = None
        best_confidence = -1.0

        for r, c, slot_img in self._iter_slot_images(panel_img, cal):
            mat_type = ""
            confidence = 0.0
            if slot_img is not None:
                info = recognizer.recognize_only(slot_img, group=group)
                if min_confidence is not None and info.confidence < min_confidence:
                    mat_type = ""
                else:
                    mat_type = info.label
                    confidence = getattr(info, 'confidence', 0.0)  # 兼容 mock 对象
            if self._match_text(mat_type, target_value, match_mode):
                if full:
                    # 全量模式：记录最高置信度的命中项
                    if confidence > best_confidence:
                        best_pos = {"row": r + 1, "col": c + 1}
                        best_confidence = confidence
                    logger.debug(f"full by panel: [{scene_key}.{panel_key}] row={r+1}, col={c+1} type={mat_type!r} confidence={confidence:.3f}")
                else:
                    # 短路模式：首个命中即返回
                    self.variables[var_name] = {"row": r + 1, "col": c + 1}
                    logger.info(f"recognize panel by [{scene_key}.{panel_key}] matched at row={r+1}, col={c+1}: {mat_type!r}")
                    return

        if full and best_pos is not None:
            self.variables[var_name] = best_pos
            logger.info(f"recognize panel full by [{scene_key}.{panel_key}] matched at row={best_pos['row']}, col={best_pos['col']} confidence={best_confidence:.3f}")
            return

        self.variables[var_name] = {}
        logger.info(f"recognize panel by [{scene_key}.{panel_key}] no match")

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
        panel = next((p for p in panels if p.key == panel_key), None)
        if panel is not None:
            require_enabled(panel, scene_key, "panel")
        return panel

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
