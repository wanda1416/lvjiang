"""识别混入类 - OCR 文字识别 + 材料识别"""

from PyQt6.QtWidgets import QApplication
from loguru import logger

from ...core.region_config import get_scene_name, get_region_defs


class RecognitionOpsMixin:
    """OCR / 材料识别混入类

    依赖主类提供:
        _tabs, _result_text, _status_bar,
        _current_scene_key (property), _current_scene_tab()
    """

    # ─── OCR 文字识别 ────────────────────────────────────

    def _on_recognize(self):
        """对当前 Tab 场景的所有已定义区域逐个裁剪识别（叠加画布变换）"""
        current_tab = self._tabs.get(self._current_scene_key)
        if current_tab is None:
            return
        regions = current_tab.get_regions()
        if not regions:
            self._status_bar.showMessage("没有已定义的区域")
            return
        if current_tab.canvas.pixmap is None:
            self._status_bar.showMessage("当前场景无截图，请先刷新截图")
            return
        image = current_tab.canvas.get_image()
        if image is None:
            self._status_bar.showMessage("当前场景无截图")
            return

        self._status_bar.showMessage("正在识别...")
        QApplication.processEvents()

        from ...core.ocr import OCREngine
        engine = OCREngine()
        canvas = current_tab.get_canvas_config()

        results = engine.ocr_scene_regions(image, canvas, regions, current_tab.scene_key)

        # 展示结果
        self._result_text.clear()
        for key, text in results.items():
            # 查找中文名
            name = key
            for r in regions:
                if r.key == key:
                    name = r.name
                    break
            self._result_text.append(f"{name}: {text or '(未识别到)'}")

        self._status_bar.showMessage(f"识别完成，共 {len(results)} 个字段")
        logger.info(
            f"OCR 识别完成 (场景={get_scene_name(current_tab.scene_key)}): {results}"
        )

    # ─── 材料识别 ────────────────────────────────────────

    def _on_recognize_materials(self):
        """对当前 Tab 场景中所有 type==slot 的区域做材料识别"""
        current_tab = self._tabs.get(self._current_scene_key)
        if current_tab is None:
            return
        regions = current_tab.get_regions()
        if not regions:
            self._status_bar.showMessage("没有已定义的区域")
            return
        if current_tab.canvas.pixmap is None:
            self._status_bar.showMessage("当前场景无截图，请先刷新截图")
            return
        image = current_tab.canvas.get_image()
        if image is None:
            self._status_bar.showMessage("当前场景无截图")
            return

        # 筛选 slot 类型区域
        slot_keys = {r.key for r in get_region_defs(current_tab.scene_key) if r.type == "slot"}
        slot_regions = [r for r in regions if r.key in slot_keys]
        if not slot_regions:
            self._status_bar.showMessage("当前场景没有 slot 类型的区域")
            return

        self._status_bar.showMessage("正在识别材料...")
        QApplication.processEvents()

        from ...core.material_recognizer import MaterialRecognizer
        from ...core.ocr import OCREngine
        ocr_engine = OCREngine()
        recognizer = MaterialRecognizer(ocr_engine)
        canvas = current_tab.get_canvas_config()
        h, w = image.shape[:2]
        canvas_x = canvas.x_ratio * w
        canvas_y = canvas.y_ratio * h
        canvas_w = canvas.w_ratio * w
        canvas_h = canvas.h_ratio * h

        # 展示结果
        self._result_text.clear()
        for region in slot_regions:
            x1 = int(canvas_x + region.x_ratio * canvas_w)
            y1 = int(canvas_y + region.y_ratio * canvas_h)
            x2 = int(canvas_x + (region.x_ratio + region.w_ratio) * canvas_w)
            y2 = int(canvas_y + (region.y_ratio + region.h_ratio) * canvas_h)
            crop = image[y1:y2, x1:x2]
            info = recognizer.recognize(crop)

            if not info.type:
                line = f"{region.name}: (空槽)"
            else:
                parts = [info.type]
                if info.level is not None:
                    parts.append(f"{info.level}级")
                if info.count is not None:
                    count_str = f"×{info.count}"
                    if info.owned is not None:
                        count_str += f"/{info.owned}"
                    parts.append(count_str)
                parts.append(f"[{info.confidence:.0%}]")
                line = f"{region.name}: {' '.join(parts)}"
            self._result_text.append(line)

        self._status_bar.showMessage(f"材料识别完成，共 {len(slot_regions)} 个槽位")
        logger.info(
            f"材料识别完成 (场景={get_scene_name(current_tab.scene_key)}, "
            f"槽位数={len(slot_regions)})"
        )
