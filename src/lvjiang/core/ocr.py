"""OCR 引擎封装 - RapidOCR (ONNX Runtime) 封装，懒加载

所有 OCR 输出均经过通用清洗规则处理（config/system/ocr_rules.yaml）。
"""

from dataclasses import dataclass

import numpy as np
from loguru import logger

from ..workflows.align import GridAlignment
from .layout_models import CanvasConfig, Region
from .ocr_cleaner import OCRCleaner
from .scene_registry import get_region_defs


@dataclass
class OCRResult:
    """单条 OCR 识别结果"""
    text: str
    confidence: float
    bbox: list[tuple[int, int]]  # 四角坐标 [(x,y), ...]


class OCREngine:
    """RapidOCR 引擎（单例，懒加载）"""

    _instance = None

    def __init__(self):
        self._ocr = None
        self._available = False

    def _ensure_loaded(self) -> bool:
        """懒加载 RapidOCR，首次调用时初始化"""
        if self._ocr is not None:
            return self._available
        try:
            from rapidocr_onnxruntime import RapidOCR
            self._ocr = RapidOCR()
            self._available = True
            logger.info("RapidOCR 引擎加载成功（ONNX Runtime）")
        except ImportError as e:
            logger.error(
                f"RapidOCR 未安装: {e}\n"
                "请执行: pip install rapidocr-onnxruntime"
            )
            self._available = False
        except Exception as e:
            logger.error(f"RapidOCR 初始化失败: {e}")
            self._available = False
        return self._available

    def recognize(self, image: np.ndarray) -> list[OCRResult]:
        """
        对整张图做 OCR
        image: BGR 格式的 numpy 数组
        返回: list[OCRResult]
        """
        if not self._ensure_loaded():
            return []
        try:
            # 图像预处理：提升 OCR 准确率
            processed = self._preprocess_for_ocr(image)
            result, _ = self._ocr(processed)
            return self._parse_result(result)
        except Exception as e:
            logger.error(f"OCR 识别失败: {e}")
            return []

    @staticmethod
    def _preprocess_for_ocr(image: np.ndarray) -> np.ndarray:
        """图像预处理：直接返回原图

        RapidOCR 内部已有预处理和 resize 逻辑，
        游戏截图是像素级精准的数子画面，无需额外锐化/对比度增强。
        """
        return image

    @staticmethod
    def _parse_result(result) -> list[OCRResult]:
        """将 RapidOCR 原始结果解析为 OCRResult 列表

        RapidOCR 返回: list of [bbox, text, confidence]
        bbox: [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]

        所有文本均经过通用清洗规则处理。
        """
        if not result:
            return []
        cleaner = OCRCleaner()
        parsed = []
        for bbox_raw, text, conf in result:
            bbox = [(int(p[0]), int(p[1])) for p in bbox_raw]
            cleaned_text = cleaner.clean(text)
            parsed.append(OCRResult(text=cleaned_text, confidence=float(conf), bbox=bbox))
        return parsed

    def ocr_scene_regions(
        self,
        image: np.ndarray,
        canvas: CanvasConfig,
        regions: list[Region],
        scene_key: str,
        min_confidence: float | None = None,
    ) -> dict[str, str]:
        """
        对指定场景的所有区域逐个裁剪 OCR。

        跳过 is_text == False 的字段。

        Args:
            image: 截图 numpy 数组 (BGR)
            canvas: 画布配置（用于坐标变换）
            regions: 该场景的区域列表
            scene_key: 场景 key，用于获取字段定义
            min_confidence: 可选，置信度阈值，过滤低于阈值的 OCR 结果

        Returns:
            dict[region.key, ocr_text]
        """
        h, w = image.shape[:2]
        canvas_x = canvas.x_ratio * w
        canvas_y = canvas.y_ratio * h
        canvas_w = canvas.w_ratio * w
        canvas_h = canvas.h_ratio * h

        # 获取 is_text 为 True 的区域集合
        text_keys = {r.key for r in get_region_defs(scene_key) if r.is_text}

        results: dict[str, str] = {}
        for region in regions:
            if region.key not in text_keys:
                logger.debug(f"跳过非文字区域: {region.key}")
                continue

            # 区域归一化坐标 -> 截图像素
            x1 = int(canvas_x + region.x_ratio * canvas_w)
            y1 = int(canvas_y + region.y_ratio * canvas_h)
            x2 = int(canvas_x + (region.x_ratio + region.w_ratio) * canvas_w)
            y2 = int(canvas_y + (region.y_ratio + region.h_ratio) * canvas_h)

            crop = image[y1:y2, x1:x2]
            ocr_results = self.recognize(crop)
            if min_confidence is not None:
                ocr_results = [r for r in ocr_results if r.confidence >= min_confidence]
            text = " | ".join(r.text for r in ocr_results) if ocr_results else ""
            results[region.key] = text

        return results

    def ocr_single(self, image: np.ndarray, min_confidence: float | None = None) -> str:
        """对单张小图做 OCR，返回清洗后的文本（多条用 | 分隔）"""
        ocr_results = self.recognize(image)
        if min_confidence is not None:
            ocr_results = [r for r in ocr_results if r.confidence >= min_confidence]
        return " | ".join(r.text for r in ocr_results) if ocr_results else ""

    def calibrate_panel_cells(
        self,
        image: np.ndarray,
        canvas: CanvasConfig,
        panel,
    ) -> list[tuple[int, int, int, int]]:
        """校准面板网格，返回每个 cell 的像素坐标 (x1, y1, x2, y2)

        Args:
            image: 全截图 numpy 数组 (BGR)
            canvas: 画布配置（用于坐标变换）
            panel: Panel 对象（携带 rows/cols/calibration 等参数）

        Returns:
            list of (x1, y1, x2, y2) 像素坐标，按 row-major 顺序
        """
        from ..workflows.align import _make_even_alignment, detect_grid

        h, w = image.shape[:2]
        # panel 区域像素坐标
        px1 = int(canvas.x_ratio * w + panel.x_ratio * canvas.w_ratio * w)
        py1 = int(canvas.y_ratio * h + panel.y_ratio * canvas.h_ratio * h)
        px2 = int(canvas.x_ratio * w + (panel.x_ratio + panel.w_ratio) * canvas.w_ratio * w)
        py2 = int(canvas.y_ratio * h + (panel.y_ratio + panel.h_ratio) * canvas.h_ratio * h)
        panel_img = image[py1:py2, px1:px2]

        # 校准模式
        calibration = getattr(panel, "calibration", "auto")
        alignment: GridAlignment | None
        if calibration == "even":
            alignment = _make_even_alignment(panel.rows, panel.cols)
        else:
            fallback = (calibration == "auto")
            alignment = detect_grid(
                panel_img,
                expected_rows=panel.rows,
                expected_cols=panel.cols,
                fallback=fallback,
                scroll_direction=getattr(panel, "scroll_direction", "vertical"),
            )

        if alignment is None:
            return []

        # 将归一化 cell 坐标转为全图像素坐标
        cells = []
        panel_w = px2 - px1
        panel_h = py2 - py1
        for r in range(alignment.n_rows):
            for c in range(alignment.n_cols):
                nx1, ny1, nx2, ny2 = alignment.slot_bounds(r, c)
                cx1 = px1 + int(nx1 * panel_w)
                cy1 = py1 + int(ny1 * panel_h)
                cx2 = px1 + int(nx2 * panel_w)
                cy2 = py1 + int(ny2 * panel_h)
                cells.append((cx1, cy1, cx2, cy2))
        return cells
