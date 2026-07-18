"""OCR 引擎封装 - RapidOCR (ONNX Runtime) 封装，懒加载"""

from dataclasses import dataclass
import numpy as np
from loguru import logger

from .region_config import CanvasConfig, Region, get_region_defs


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
        """图像预处理：提升文字识别准确率

        对裁剪区域做锐化 + 对比度增强，帮助 OCR 引擎识别文字。
        保留颜色信息，避免灰度化导致颜色区分丢失。
        """
        import cv2

        # 1. 锐化（Unsharp Mask）— 增强文字边缘
        blurred = cv2.GaussianBlur(image, (0, 0), 3)
        sharpened = cv2.addWeighted(image, 1.5, blurred, -0.5, 0)

        # 2. 对比度增强（CLAHE）— 提升文字/背景区分度
        # CLAHE 需要在 LAB 空间的 L 通道操作，保留颜色信息
        lab = cv2.cvtColor(sharpened, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        l_enhanced = clahe.apply(l)
        enhanced_lab = cv2.merge([l_enhanced, a, b])
        result = cv2.cvtColor(enhanced_lab, cv2.COLOR_LAB2BGR)

        return result

    @staticmethod
    def _parse_result(result) -> list[OCRResult]:
        """将 RapidOCR 原始结果解析为 OCRResult 列表
        RapidOCR 返回: list of [bbox, text, confidence]
        bbox: [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
        """
        if not result:
            return []
        parsed = []
        for bbox_raw, text, conf in result:
            bbox = [(int(p[0]), int(p[1])) for p in bbox_raw]
            parsed.append(OCRResult(text=text, confidence=float(conf), bbox=bbox))
        return parsed

    def ocr_scene_regions(
        self,
        image: np.ndarray,
        canvas: CanvasConfig,
        regions: list[Region],
        scene_key: str,
    ) -> dict[str, str]:
        """
        对指定场景的所有区域逐个裁剪 OCR。

        跳过 is_text == False 的字段。

        Args:
            image: 截图 numpy 数组 (BGR)
            canvas: 画布配置（用于坐标变换）
            regions: 该场景的区域列表
            scene_key: 场景 key，用于获取字段定义

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
            text = " | ".join(r.text for r in ocr_results) if ocr_results else ""
            results[region.key] = text

        return results
