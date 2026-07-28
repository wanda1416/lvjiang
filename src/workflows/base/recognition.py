"""截图 / OCR / 材料识别与 by 短路识别"""

import numpy as np
from loguru import logger

from ...core.scene_registry import Region, CanvasConfig


class _RecognitionMixin:
    """截图与识别能力（OCR / 参考图匹配 / by 短路）"""

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

    # ─── 材料识别 ──────────────────────────────────────────

    def recognize_materials(
        self,
        scene_key: str,
        slot_keys: list[str] | None = None,
        group: str | None = None,
    ) -> tuple[dict[str, str], dict]:
        """对指定场景的每个 slot 执行参考图匹配

        Args:
            scene_key: 场景 key
            slot_keys: 可选，只识别指定 slot
            group: 可选，限定参考图分组范围

        Returns:
            (result, region_map)
            result: {slot_key: label, ...}  空槽为 ""
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
                f"参考图匹配 [{scene_key}].[{region.key}]: "
                f"label={info.type!r} level={info.level} count={info.count}"
            )

        fields_display = slot_keys if slot_keys else [r.key for r in self._layout.get_scene_regions(scene_key)]
        logger.info(f"参考图匹配 [{scene_key}]:{fields_display} => {result}")
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
