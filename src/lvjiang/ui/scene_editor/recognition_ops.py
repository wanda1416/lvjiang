"""识别混入类 - OCR 文字识别 + 材料识别（区域/面板自动分发）"""

from loguru import logger
from PyQt6.QtWidgets import QApplication

from ...core.scene_registry import get_region_defs, get_region_name, get_scene_name
from ...i18n import tr


class RecognitionOpsMixin:
    """OCR / 材料识别混入类

    依赖主类提供:
        _tabs, _result_text, _status_bar,
        _current_scene_key (property), _current_scene_tab(),
        _chk_live_image, _combo_mat_group, _refresh_callback
    """

    def _is_panel_tab_active(self, current_tab) -> bool:
        """检查当前激活的是否是面板列表 Tab"""
        if not hasattr(current_tab, '_right_tabs'):
            return False
        # 右侧 Tab 顺序：0=区域列表, 1=坐标列表, 2=方向列表, 3=面板列表
        return current_tab._right_tabs.currentIndex() == 3

    def _get_recognition_image(self, current_tab):
        """获取识别用图：勾选实时图像时从设备截屏，否则用缓存截图

        Returns:
            (image, error_msg): image 为 ndarray 或 None
        """
        use_live = hasattr(self, '_chk_live_image') and self._chk_live_image.isChecked()

        if use_live:
            # 实时截屏模式：从设备获取最新截图，不保存到场景
            if not hasattr(self, '_refresh_callback') or self._refresh_callback is None:
                return None, tr("无截图源，请先在主窗口定位窗口")
            result = self._refresh_callback()
            new_image, error_msg = result if isinstance(result, tuple) else (result, None)
            if new_image is None:
                return None, error_msg or tr("实时截屏失败")
            return new_image, None
        else:
            # 缓存截图模式：使用当前场景的截图
            if current_tab.canvas.pixmap is None:
                return None, tr("当前场景无截图，请先刷新截图")
            image = current_tab.canvas.get_image()
            if image is None:
                return None, tr("当前场景无截图")
            return image, None

    # ─── OCR 文字识别 ────────────────────────────────────

    def _on_recognize(self):
        """对当前 Tab 场景的区域或面板做 OCR 文字识别（根据激活的列表自动分发）"""
        current_tab = self._tabs.get(self._current_scene_key)
        if current_tab is None:
            return

        # 根据激活的 Tab 决定识别目标
        if self._is_panel_tab_active(current_tab):
            self._on_recognize_panel_ocr(current_tab)
        else:
            self._on_recognize_region_ocr(current_tab)

    def _on_recognize_region_ocr(self, current_tab):
        """区域 OCR 文字识别"""
        regions = current_tab.get_visible_regions()
        if not regions:
            self._status_bar.showMessage(tr("没有已定义的区域"))
            return

        # 获取识别用图（实时截屏或缓存截图）
        image, error_msg = self._get_recognition_image(current_tab)
        if image is None:
            self._status_bar.showMessage(error_msg or tr("获取图像失败"))
            return

        self._status_bar.showMessage(tr("正在识别..."))
        QApplication.processEvents()

        from ...core.ocr import OCREngine
        engine = OCREngine()
        canvas = current_tab.get_canvas_config()

        results = engine.ocr_scene_regions(image, canvas, regions, current_tab.scene_key)

        # 展示结果（按场景定义顺序，仅显示 is_text=True 的区域）
        self._result_text.clear()
        for region in regions:
            if region.key not in results:
                continue
            text = results[region.key]
            name = get_region_name(current_tab.scene_key, region.key)
            self._result_text.append(f"{name}: {text or tr('(未识别到)')}")

        self._status_bar.showMessage(f"识别完成，共 {len(results)} 个字段")
        logger.info(
            f"OCR 识别完成 (场景={get_scene_name(current_tab.scene_key)}): {results}"
        )

    def _on_recognize_panel_ocr(self, current_tab):
        """面板 OCR 文字识别（逐 cell 识别）"""
        panels = current_tab.get_visible_panels()
        if not panels:
            self._status_bar.showMessage(tr("没有已定义的面板"))
            return

        # 获取识别用图
        image, error_msg = self._get_recognition_image(current_tab)
        if image is None:
            self._status_bar.showMessage(error_msg or tr("获取图像失败"))
            return

        self._status_bar.showMessage(tr("正在校准面板网格..."))
        QApplication.processEvents()

        from ...core.ocr import OCREngine
        engine = OCREngine()
        canvas_config = current_tab.get_canvas_config()

        self._result_text.clear()
        total_cells = 0
        for panel in panels:
            # 校准网格，获取每个 cell 的位置
            cells = engine.calibrate_panel_cells(image, canvas_config, panel)
            if not cells:
                self._result_text.append(f"[{panel.key}] 校准失败，跳过")
                continue

            self._result_text.append(f"[{panel.key}] {panel.rows}×{panel.cols} = {len(cells)} 个 cell")
            for i, (x1, y1, x2, y2) in enumerate(cells):
                crop = image[y1:y2, x1:x2]
                text = engine.ocr_single(crop)
                row = i // panel.cols + 1
                col = i % panel.cols + 1
                if text:
                    self._result_text.append(f"  cell[{row}][{col}]: {text}")
                    total_cells += 1

        self._status_bar.showMessage(f"面板识别完成，共 {total_cells} 个 cell")
        logger.info(f"面板 OCR 识别完成 (场景={get_scene_name(current_tab.scene_key)}, 面板数={len(panels)})")

    # ─── 材料识别 ────────────────────────────────────────

    def _get_mat_group(self) -> str | None:
        """从材料分组下拉框获取选中的分组，None 表示全部"""
        if hasattr(self, '_combo_mat_group'):
            return self._combo_mat_group.currentData()
        return None

    def _on_recognize_materials(self):
        """对当前 Tab 场景的区域或面板做材料识别（根据激活的列表自动分发）"""
        current_tab = self._tabs.get(self._current_scene_key)
        if current_tab is None:
            return

        # 根据激活的 Tab 决定识别目标
        if self._is_panel_tab_active(current_tab):
            self._on_recognize_panel_materials(current_tab)
        else:
            self._on_recognize_region_materials(current_tab)

    def _on_recognize_region_materials(self, current_tab):
        """区域材料识别（type==slot 的区域）"""
        regions = current_tab.get_visible_regions()
        if not regions:
            self._status_bar.showMessage(tr("没有已定义的区域"))
            return

        # 筛选 slot 类型区域
        slot_keys = {r.key for r in get_region_defs(current_tab.scene_key) if r.type == "slot"}
        slot_regions = [r for r in regions if r.key in slot_keys]
        if not slot_regions:
            self._status_bar.showMessage(tr("当前场景没有 slot 类型的区域"))
            return

        # 获取识别用图（实时截屏或缓存截图）
        image, error_msg = self._get_recognition_image(current_tab)
        if image is None:
            self._status_bar.showMessage(error_msg or tr("获取图像失败"))
            return

        self._status_bar.showMessage(tr("正在识别材料..."))
        QApplication.processEvents()

        from lvjiang.apps.yysls.core.recognizer.material_recognizer import MaterialRecognizer

        from ...core.ocr import OCREngine
        ocr_engine = OCREngine()
        recognizer = MaterialRecognizer(ocr_engine)
        # 非预制输入字段（用于展示匹配条目的元数据）
        input_fields = recognizer.reference_db.get_custom_input_fields()
        canvas = current_tab.get_canvas_config()
        h, w = image.shape[:2]
        canvas_x = canvas.x_ratio * w
        canvas_y = canvas.y_ratio * h
        canvas_w = canvas.w_ratio * w
        canvas_h = canvas.h_ratio * h

        # 展示结果
        self._result_text.clear()
        group = self._get_mat_group()
        for region in slot_regions:
            x1 = int(canvas_x + region.x_ratio * canvas_w)
            y1 = int(canvas_y + region.y_ratio * canvas_h)
            x2 = int(canvas_x + (region.x_ratio + region.w_ratio) * canvas_w)
            y2 = int(canvas_y + (region.y_ratio + region.h_ratio) * canvas_h)
            crop = image[y1:y2, x1:x2]
            info = recognizer.recognize(crop, group=group)

            if not info.type:
                line = f"{get_region_name(current_tab.scene_key, region.key)}: (空槽)"
            else:
                parts = [info.type]
                # 输入元数据（匹配条目的属性，如 level=110）
                for f in input_fields:
                    value = info.meta.get(f.key)
                    if value is not None:
                        parts.append(f"{f.key}={value}")
                # 输出元数据 OCR 原始文本（如 level_text=110阶 count_text=0/691）
                for key, text in info.ocr_texts.items():
                    parts.append(f"{key}={text or tr('(无)')}")
                # 解析属性
                if info.real_level is not None:
                    parts.append(f"real_level={info.real_level}")
                parts.append(f"[{info.confidence:.0%}]")
                line = f"{get_region_name(current_tab.scene_key, region.key)}: {' '.join(parts)}"
            self._result_text.append(line)

        self._status_bar.showMessage(f"材料识别完成，共 {len(slot_regions)} 个槽位")
        logger.info(
            f"材料识别完成 (场景={get_scene_name(current_tab.scene_key)}, "
            f"槽位数={len(slot_regions)})"
        )

    def _on_recognize_panel_materials(self, current_tab):
        """面板材料识别（逐 cell 识别）"""
        panels = current_tab.get_visible_panels()
        if not panels:
            self._status_bar.showMessage(tr("没有已定义的面板"))
            return

        # 获取识别用图
        image, error_msg = self._get_recognition_image(current_tab)
        if image is None:
            self._status_bar.showMessage(error_msg or tr("获取图像失败"))
            return

        self._status_bar.showMessage(tr("正在校准面板网格..."))
        QApplication.processEvents()

        from lvjiang.apps.yysls.core.recognizer.material_recognizer import MaterialRecognizer

        from ...core.ocr import OCREngine

        ocr_engine = OCREngine()
        recognizer = MaterialRecognizer(ocr_engine)
        # 非预制输入字段（用于展示匹配条目的元数据）
        input_fields = recognizer.reference_db.get_custom_input_fields()
        canvas_config = current_tab.get_canvas_config()

        self._result_text.clear()
        total_cells = 0
        group = self._get_mat_group()
        for panel in panels:
            # 校准网格，获取每个 cell 的位置
            cells = ocr_engine.calibrate_panel_cells(image, canvas_config, panel)
            if not cells:
                self._result_text.append(f"[{panel.key}] 校准失败，跳过")
                continue

            self._result_text.append(f"[{panel.key}] {panel.rows}×{panel.cols} = {len(cells)} 个 cell")
            for i, (x1, y1, x2, y2) in enumerate(cells):
                crop = image[y1:y2, x1:x2]
                info = recognizer.recognize(crop, group=group)
                row = i // panel.cols + 1
                col = i % panel.cols + 1

                if not info.type:
                    self._result_text.append(f"  cell[{row}][{col}]: (空)")
                else:
                    parts = [info.type]
                    # 输入元数据（匹配条目的属性，如 level=110）
                    for f in input_fields:
                        value = info.meta.get(f.key)
                        if value is not None:
                            parts.append(f"{f.key}={value}")
                    # 输出元数据 OCR 原始文本
                    for key, text in info.ocr_texts.items():
                        parts.append(f"{key}={text or tr('(无)')}")
                    self._result_text.append(f"  cell[{row}][{col}]: {' '.join(parts)}")
                total_cells += 1

        self._status_bar.showMessage(f"面板材料识别完成，共 {total_cells} 个 cell")
        logger.info(f"面板材料识别完成 (场景={get_scene_name(current_tab.scene_key)}, 面板数={len(panels)})")
