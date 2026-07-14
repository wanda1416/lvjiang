"""OCR 引擎封装 - RapidOCR (ONNX Runtime) 封装，懒加载"""

from dataclasses import dataclass
import numpy as np
from loguru import logger


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
            result, _ = self._ocr(image)
            return self._parse_result(result)
        except Exception as e:
            logger.error(f"OCR 识别失败: {e}")
            return []

    def recognize_crop(
        self, image: np.ndarray, box: tuple[int, int, int, int]
    ) -> list[OCRResult]:
        """先裁剪 (left, top, width, height)，再 OCR"""
        left, top, w, h = box
        cropped = image[top:top + h, left:left + w]
        return self.recognize(cropped)

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
