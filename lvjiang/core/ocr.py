"""OCR 引擎封装 - RapidOCR (ONNX Runtime) 封装，懒加载"""

from dataclasses import dataclass
import numpy as np
from loguru import logger

from .scene_registry import CanvasConfig, Region, get_region_defs


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
        对整张图做 OCR。多通道预处理确保不同颜色文字均可识别。
        image: BGR 格式的 numpy 数组
        返回: list[OCRResult]
        """
        if not self._ensure_loaded():
            return []
        try:
            processed = self._preprocess_for_ocr(image)
            result, _ = self._ocr(processed)
            return self._parse_result(result)
        except Exception as e:
            logger.error(f"OCR 识别失败: {e}")
            return []

    @staticmethod
    def _preprocess_for_ocr(image: np.ndarray) -> np.ndarray:
        """图像预处理：提升多色文字识别准确率

        根据图像尺寸自动选择预处理策略：
        - 小区域（纯文字裁剪）：max-channel + 二值化，简洁高效
        - 大图像（全屏截图）：分通道 CLAHE，保留多色文字对比度
        """
        import cv2

        h, w = image.shape[:2]

        # 小区域走简单二值化路径
        # 当图像足够小时（如属性文字裁剪），max-channel + 二值化
        # 比 CLAHE 更可靠：任何颜色的文字都变白，背景变黑
        if h < 150 or w < 150:
            return OCREngine._preprocess_small_region(image)

        # 大图像：分通道 CLAHE
        # 1. 锐化（Unsharp Mask）— 增强文字边缘
        blurred = cv2.GaussianBlur(image, (0, 0), 3)
        sharpened = cv2.addWeighted(image, 1.5, blurred, -0.5, 0)

        # 2. 分通道 CLAHE — 每通道独立增强对比度
        b, g, r = cv2.split(sharpened)

        # 动态 tile 尺寸：保证每个 tile ≥ 40px，最多 8x8 个 tile
        tile_size = max(40, min(h, w) // 8)
        tile_size = max(2, (tile_size // 2) * 2)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(tile_size, tile_size))

        b_enhanced = clahe.apply(b)
        g_enhanced = clahe.apply(g)
        r_enhanced = clahe.apply(r)
        return cv2.merge([b_enhanced, g_enhanced, r_enhanced])

    @staticmethod
    def _preprocess_small_region(image: np.ndarray) -> np.ndarray:
        """小区域纯文字预处理：max-channel + 二值化

        游戏 UI 文字通常是彩色字在暗色背景上：
        - max(B,G,R) 让任何颜色的文字都变亮（白）
        - 二值化得到干净的黑底白字 / 白底黑字

        这比 CLAHE 更简单直接，对小图识别率显著提升。
        """
        import cv2

        # 1. 取三通道最大值 — 任何颜色的文字都变亮
        if len(image.shape) == 3 and image.shape[2] >= 3:
            gray = cv2.max(cv2.max(image[:, :, 0], image[:, :, 1]), image[:, :, 2])
        else:
            gray = image if len(image.shape) == 2 else image[:, :, 0]

        # 2. 判断文字/背景明暗：如果文字比背景亮，反转
        # 游戏 UI 通常是暗背景 + 亮文字 → 灰度图背景暗、文字亮
        mean_val = gray.mean()
        # 如果平均亮度较低（暗背景），说明文字是亮的 → 需要反转为黑字白底
        if mean_val < 128:
            gray = 255 - gray

        # 3. 二值化（Otsu 自动阈值）
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # 4. 转回 BGR 三通道（OCR 引擎期望 BGR）
        return cv2.merge([binary, binary, binary])

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
