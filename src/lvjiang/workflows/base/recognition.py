"""截图 / OCR / 材料识别与 by 短路识别"""

import numpy as np
from loguru import logger

from ...core.scene_registry import CanvasConfig, FoundRegion, Region


class _RecognitionMixin:
    """截图与识别能力（OCR / 参考图匹配 / by 短路）"""

    # ─── 区域解析 ──────────────────────────────────────────

    def _require_regions(
        self,
        scene_key: str,
        field_keys: list[str],
        regions: list[Region],
    ) -> list[Region]:
        """按 field_keys 顺序取出 region，任一 key 未绑定坐标即报错

        显式点名的字段（含 [scene].$var 动态解析出来的）一旦在布局里
        找不到，静默跳过会让 by 子句退化成「未命中」、普通 OCR 少字段，
        流程照旧往下走，日志上完全看不出是绑定丢了。故缺失即抛错中断。

        Args:
            scene_key: 场景 key
            field_keys: 点名的字段列表（非空）
            regions: 该场景在当前布局已绑定的区域

        Returns:
            与 field_keys 同序的 Region 列表
        """
        region_map = {r.key: r for r in regions}
        missing = [k for k in field_keys if k not in region_map]
        if missing:
            raise ValueError(
                f"场景 {scene_key} 的区域未绑定坐标: {'、'.join(missing)}，"
                f"请在场景布局编辑器中绑定后重试"
            )
        return [region_map[k] for k in field_keys]

    # ─── 截图与 OCR ────────────────────────────────────────

    def ocr_scene(self, scene_key: str, field_keys: list[str] | None = None, min_confidence: float | None = None) -> dict[str, str]:
        """对指定场景执行截图 + OCR

        Args:
            scene_key: 场景 key
            field_keys: 可选，只 OCR 指定字段列表
            min_confidence: 可选，置信度阈值，过滤低于阈值的 OCR 结果

        Returns:
            {field_key: ocr_text, ...}
        """
        img = self._capture.capture()
        if img is None:
            logger.error("截图失败")
            return {}

        canvas = self._layout.get_canvas()
        regions = self._layout.get_scene_regions(scene_key)
        if field_keys:
            regions = self._require_regions(scene_key, field_keys, regions)
        elif not regions:
            logger.warning(f"场景 {scene_key} 没有定义区域")
            return {}

        result = self._ocr.ocr_scene_regions(img, canvas, regions, scene_key, min_confidence=min_confidence)
        fields_display = field_keys if field_keys else [r.key for r in self._layout.get_scene_regions(scene_key)]
        logger.debug(f"OCR [{scene_key}]:{fields_display} => {result}")
        return result

    # ─── 材料识别 ──────────────────────────────────────────

    def recognize_materials(
        self,
        scene_key: str,
        slot_keys: list[str] | None = None,
        group: str | None = None,
        min_confidence: float | None = None,
    ) -> tuple[dict[str, str], dict]:
        """对指定场景的每个 slot 执行参考图匹配

        Args:
            scene_key: 场景 key
            slot_keys: 可选，只识别指定 slot
            group: 可选，限定参考图分组范围
            min_confidence: 可选，置信度阈值，低于阈值的视为未识别

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
        if slot_keys:
            regions = self._require_regions(scene_key, slot_keys, regions)
        elif not regions:
            logger.warning(f"场景 {scene_key} 没有定义区域")
            return {}, {}

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
            if min_confidence is not None and info.confidence < min_confidence:
                result[region.key] = ""
            else:
                result[region.key] = info.type  # 空槽 info.type == ""
            logger.debug(
                f"参考图匹配 [{scene_key}].[{region.key}]: "
                f"label={info.type!r} level={info.level} count={info.count}"
            )

        fields_display = slot_keys if slot_keys else [r.key for r in self._layout.get_scene_regions(scene_key)]
        logger.info(f"参考图匹配 [{scene_key}]:{fields_display} => {result}")
        return result, region_map

    def recognize_materials_info(
        self,
        scene_key: str,
        slot_keys: list[str] | None = None,
        group: str | None = None,
    ) -> dict[str, object]:
        """完整材料识别：一次截图，逐 slot 返回识别结果对象

        与 recognize_materials 只返 label 不同，保留识别器结果的全部
        字段（type/level/count/devoted/count_text/confidence），供数量检查等策略
        消费；裁切失败的 slot 不入结果。

        Args:
            scene_key: 场景 key
            slot_keys: 可选，只识别指定 slot
            group: 可选，限定参考图分组范围

        Returns:
            {slot_key: MaterialInfo, ...}
        """
        img = self._capture.capture()
        if img is None:
            logger.error("截图失败")
            return {}

        canvas = self._layout.get_canvas()
        regions = self._layout.get_scene_regions(scene_key)
        if slot_keys:
            regions = self._require_regions(scene_key, slot_keys, regions)
        elif not regions:
            logger.warning(f"场景 {scene_key} 没有定义区域")
            return {}

        infos: dict[str, object] = {}
        for region in regions:
            crop = self._crop_region(img, region, canvas)
            if crop is None:
                logger.warning(f"slot {region.key} 裁切为空，跳过")
                continue
            info = self.material_recognizer.recognize(crop, group=group)
            infos[region.key] = info

        summary = {k: (f"{i.type}×{i.count}" if i.count is not None else i.type)
                   if i.type else "空"
                   for k, i in infos.items()}
        logger.info(f"材料识别 [{scene_key}]:{list(infos)} => {summary}")
        return infos

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
        min_confidence: float | None = None,
    ) -> str:
        """短路 OCR：一次截图，逐字段识别，首个命中即返回字段名

        Args:
            scene_key: 场景 key
            field_keys: 要识别的字段列表
            target_value: 匹配目标值（str 或 list，由 mode 决定）
            mode: equals | contains | equals_any | contains_any
            min_confidence: 可选，置信度阈值，过滤低于阈值的 OCR 结果

        Returns:
            首个命中的 field_key（str），全部未命中返回 ""

        Raises:
            ValueError: field_keys 里有 key 在当前布局未绑定坐标
        """
        self._validate_by_target(target_value, mode)

        img = self._capture.capture()
        if img is None:
            logger.error("截图失败")
            return ""

        canvas = self._layout.get_canvas()
        regions = self._layout.get_scene_regions(scene_key)
        if field_keys:
            # 按 field_keys 顺序识别，未绑定的 key 直接报错（否则会被当成未命中）
            ordered_regions = self._require_regions(scene_key, field_keys, regions)
        elif not regions:
            logger.warning(f"场景 {scene_key} 没有定义区域")
            return ""
        else:
            ordered_regions = regions

        for region in ordered_regions:
            crop = self._crop_region(img, region, canvas)
            if crop is None:
                logger.debug(f"by OCR: region {region.key} 裁剪为空，跳过")
                continue
            ocr_results = self._ocr.recognize(crop)
            if min_confidence is not None:
                ocr_results = [r for r in ocr_results if r.confidence >= min_confidence]
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
        min_confidence: float | None = None,
    ) -> str:
        """短路材料识别：一次截图，逐 slot 识别，首个命中即返回 slot 名

        Args:
            scene_key: 场景 key
            field_keys: 要识别的 slot 列表
            target_value: 匹配目标值（str 或 list，由 mode 决定）
            mode: equals | contains | equals_any | contains_any
            group: 可选，限定材料分组范围
            min_confidence: 可选，置信度阈值，低于阈值的视为未识别

        Returns:
            首个命中的 slot_key（str），全部未命中返回 ""

        Raises:
            ValueError: field_keys 里有 key 在当前布局未绑定坐标
        """
        self._validate_by_target(target_value, mode)

        img = self._capture.capture()
        if img is None:
            logger.error("截图失败")
            return ""

        canvas = self._layout.get_canvas()
        regions = self._layout.get_scene_regions(scene_key)
        if field_keys:
            ordered_regions = self._require_regions(scene_key, field_keys, regions)
        elif not regions:
            logger.warning(f"场景 {scene_key} 没有定义区域")
            return ""
        else:
            ordered_regions = regions

        for region in ordered_regions:
            crop = self._crop_region(img, region, canvas)
            if crop is None:
                logger.debug(f"by 材料识别: region {region.key} 裁剪为空，跳过")
                continue
            info = self.material_recognizer.recognize(crop, group=group)
            if min_confidence is not None and info.confidence < min_confidence:
                logger.debug(f"by 材料识别: region {region.key} 置信度 {info.confidence:.3f} < {min_confidence}，跳过")
                continue
            if self._match_text(info.type, target_value, mode):
                logger.info(f"by 材料识别命中: [{scene_key}].[{region.key}] type={info.type!r} mode={mode} group={group}")
                return region.key

        logger.info(f"by 材料识别未命中: [{scene_key}]:{field_keys} mode={mode} group={group}")
        return ""

    # ─── find 指令：文字搜索 ─────────────────────────────────

    def find_text_in_region(
        self,
        target_value,
        mode: str,
        search_region: Region | None = None,
        min_confidence: float | None = None,
    ) -> FoundRegion | str:
        """在指定区域或全画布搜索目标文字，返回文字的画布归一化坐标区域

        Args:
            target_value: 搜索目标（str 或 list，由 mode 决定）
            mode: equals | contains | equals_any | contains_any
            search_region: 搜索区域（None 表示搜索全画布）
            min_confidence: 可选，置信度阈值，过滤低于阈值的 OCR 结果

        Returns:
            命中: FoundRegion（画布归一化坐标）
            未命中: 空字符串 ""
        """
        self._validate_by_target(target_value, mode)

        img = self._capture.capture()
        if img is None:
            logger.error("find: 截图失败")
            return ""

        canvas = self._layout.get_canvas()
        h, w = img.shape[:2]

        # 计算搜索区域的像素坐标与画布偏移
        canvas_px_x = canvas.x_ratio * w
        canvas_px_y = canvas.y_ratio * h
        canvas_px_w = canvas.w_ratio * w
        canvas_px_h = canvas.h_ratio * h

        if search_region is not None:
            # 指定区域：裁剪后 OCR
            crop_x1 = int(canvas_px_x + search_region.x_ratio * canvas_px_w)
            crop_y1 = int(canvas_px_y + search_region.y_ratio * canvas_px_h)
            crop_x2 = int(canvas_px_x + (search_region.x_ratio + search_region.w_ratio) * canvas_px_w)
            crop_y2 = int(canvas_px_y + (search_region.y_ratio + search_region.h_ratio) * canvas_px_h)
            crop = img[crop_y1:crop_y2, crop_x1:crop_x2]
            if crop.size == 0:
                logger.warning("find: 搜索区域裁剪为空")
                return ""
        else:
            # 全画布搜索
            crop_x1, crop_y1 = int(canvas_px_x), int(canvas_px_y)
            crop_x2, crop_y2 = int(canvas_px_x + canvas_px_w), int(canvas_px_y + canvas_px_h)
            crop = img[crop_y1:crop_y2, crop_x1:crop_x2]

        ocr_results = self._ocr.recognize(crop)
        if not ocr_results:
            logger.debug("find: OCR 无结果")
            return ""

        # 置信度过滤
        if min_confidence is not None:
            ocr_results = [r for r in ocr_results if r.confidence >= min_confidence]
            if not ocr_results:
                logger.debug(f"find: 置信度过滤后无结果（阈值 {min_confidence}）")
                return ""

        # 遍历 OCR 结果，找第一个匹配的文字
        for ocr_result in ocr_results:
            text = ocr_result.text
            if self._match_text(text, target_value, mode):
                # 将 OCR bbox（裁剪图内像素坐标）转为画布归一化坐标
                # bbox 是四角坐标 [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
                xs = [p[0] for p in ocr_result.bbox]
                ys = [p[1] for p in ocr_result.bbox]
                min_x, max_x = min(xs), max(xs)
                min_y, max_y = min(ys), max(ys)

                # 裁剪图内像素 → 全图像素 → 画布归一化
                full_x1 = crop_x1 + min_x
                full_y1 = crop_y1 + min_y
                full_x2 = crop_x1 + max_x
                full_y2 = crop_y1 + max_y

                ratio_x1 = (full_x1 - canvas_px_x) / canvas_px_w
                ratio_y1 = (full_y1 - canvas_px_y) / canvas_px_h
                ratio_x2 = (full_x2 - canvas_px_x) / canvas_px_w
                ratio_y2 = (full_y2 - canvas_px_y) / canvas_px_h

                found = FoundRegion(
                    x_ratio=ratio_x1,
                    y_ratio=ratio_y1,
                    w_ratio=ratio_x2 - ratio_x1,
                    h_ratio=ratio_y2 - ratio_y1,
                    text=text,
                )
                logger.info(
                    f"find 命中: text={text!r} mode={mode} "
                    f"region=({ratio_x1:.3f},{ratio_y1:.3f},{ratio_x2 - ratio_x1:.3f},{ratio_y2 - ratio_y1:.3f})"
                )
                return found

        logger.debug(f"find 未命中: target={target_value!r} mode={mode}")
        return ""
