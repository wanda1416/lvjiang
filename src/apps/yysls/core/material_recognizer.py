"""材料分类器 - 识别材料槽中的材料类型、等级和数量

识别策略：
- 类型识别：使用通用 ReferenceMatcher（ORB 特征匹配）
- 等级识别：OCR 裁剪左上角区域（游戏专属）
- 数量识别：OCR 裁剪右下角区域（游戏专属）
- 空槽检测：图像方差 + 饱和度判断（游戏专属）

参考库数据源：ReferenceDatabase（references.yaml）。
"""

import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from loguru import logger
from PIL import Image

from src.core.ocr import OCREngine
from src.core.reference_db import ReferenceDatabase
from src.core.recognizers.reference_matcher import ReferenceMatcher


@dataclass
class MaterialInfo:
    """单个材料槽识别结果（游戏专属）"""
    type: str          # 材料类型（如 "定音石"），空槽为 ""
    level: int | None  # 等级（如 100），无等级标识为 None
    count: int | None  # 投入数量（x/y 中的 x），无法识别为 None
    owned: int | None  # 持有数量（x/y 中的 y），无法识别为 None
    confidence: float  # 类型匹配置信度 0~1


class MaterialRecognizer:
    """材料槽内容识别器（游戏专属）

    使用通用 ReferenceMatcher 做类型识别，
    游戏专属逻辑：OCR 等级/数量、空槽判定。

    用法：
        recognizer = MaterialRecognizer(ocr_engine)
        result = recognizer.recognize(slot_img)
        # result.type -> "定音石"
        # result.level -> 100
        # result.count -> 5   (投入数量)
        # result.owned -> 20  (持有数量)
    """

    # ── 子区域比例配置（游戏专属）──────────────────────────────
    # 等级文字区域（左上角）
    LEVEL_REGION = (0.0, 0.0, 0.45, 0.30)
    # 数量文字区域（右下角）
    COUNT_REGION = (0.45, 0.70, 0.55, 0.30)
    # 空槽判定：像素方差阈值
    EMPTY_VARIANCE_THRESHOLD = 50.0

    def __init__(
        self,
        ocr_engine: OCREngine,
        reference_db: ReferenceDatabase | None = None,
    ):
        self._ocr = ocr_engine
        self._db = reference_db or ReferenceDatabase()
        
        # 使用通用 ReferenceMatcher 做类型识别
        self._matcher = ReferenceMatcher(self._db)

    @property
    def reference_db(self) -> ReferenceDatabase:
        return self._db

    @property
    def matcher(self) -> ReferenceMatcher:
        return self._matcher

    # ─── 核心识别 ──────────────────────────────────────────

    def recognize(
        self,
        slot_img: np.ndarray,
        group: str | None = None,
    ) -> MaterialInfo:
        """识别单个材料槽

        Args:
            slot_img: 材料槽裁剪图（BGR numpy 数组）
            group: 限定匹配范围到指定分组，None 表示匹配所有分组

        Returns:
            MaterialInfo
        """
        # 1. 空槽检测（游戏专属）
        if self._is_empty(slot_img):
            return MaterialInfo(type="", level=None, count=None, owned=None, confidence=1.0)

        # 2. 类型识别（通用 ReferenceMatcher）
        match_result = self._matcher.match(slot_img, group=group)
        mat_type = match_result.label
        confidence = match_result.confidence

        if not mat_type:
            return MaterialInfo(type="", level=None, count=None, owned=None, confidence=confidence)

        # 3. 等级识别（OCR 左上角，游戏专属）
        level = self._ocr_level(slot_img)

        # 4. 数量识别（OCR 右下角，游戏专属）
        count, owned = self._ocr_count(slot_img)

        return MaterialInfo(
            type=mat_type,
            level=level,
            count=count,
            owned=owned,
            confidence=confidence,
        )

    def recognize_batch(
        self,
        slot_images: dict[str, np.ndarray],
    ) -> dict[str, MaterialInfo]:
        """批量识别多个材料槽

        Args:
            slot_images: {slot_key: slot_img, ...}

        Returns:
            {slot_key: MaterialInfo, ...}
        """
        return {
            key: self.recognize(img)
            for key, img in slot_images.items()
        }

    # ─── 空槽检测（游戏专属）───────────────────────────────────

    def _is_empty(self, img: np.ndarray) -> bool:
        """判断材料槽是否为空"""
        if img is None or img.size == 0:
            return True

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        variance = cv2.Laplacian(gray, cv2.CV_64F).var()

        if variance < self.EMPTY_VARIANCE_THRESHOLD:
            logger.debug(f"空槽检测: 方差={variance:.1f} < {self.EMPTY_VARIANCE_THRESHOLD}")
            return True
        return False

    # ─── 等级 OCR（游戏专属）───────────────────────────────────

    def _ocr_level(self, slot_img: np.ndarray) -> int | None:
        """OCR 识别左上角等级"""
        h, w = slot_img.shape[:2]
        x1, y1 = int(self.LEVEL_REGION[0] * w), int(self.LEVEL_REGION[1] * h)
        x2, y2 = int((self.LEVEL_REGION[0] + self.LEVEL_REGION[2]) * w), \
                 int((self.LEVEL_REGION[1] + self.LEVEL_REGION[3]) * h)

        crop = slot_img[y1:y2, x1:x2]
        if crop.size == 0:
            return None

        text = self._ocr_single(crop)
        if not text:
            return None

        # 提取数字（等级通常是纯数字，如 "100"）
        nums = re.findall(r"\d+", text)
        if nums:
            level = int(nums[0])
            logger.debug(f"等级识别: {text!r} -> {level}")
            return level

        logger.debug(f"等级识别: 无法从 {text!r} 中提取数字")
        return None

    # ─── 数量 OCR（游戏专属）───────────────────────────────────

    def _ocr_count(self, slot_img: np.ndarray) -> tuple[int | None, int | None]:
        """OCR 识别右下角数量

        右下角格式为 x/y：
        - x = 投入数量
        - y = 持有数量

        Returns:
            (count, owned)，无法识别的字段为 None
        """
        h, w = slot_img.shape[:2]
        x1, y1 = int(self.COUNT_REGION[0] * w), int(self.COUNT_REGION[1] * h)
        x2, y2 = int((self.COUNT_REGION[0] + self.COUNT_REGION[2]) * w), \
                 int((self.COUNT_REGION[1] + self.COUNT_REGION[3]) * h)

        crop = slot_img[y1:y2, x1:x2]
        if crop.size == 0:
            return None, None

        text = self._ocr_single(crop)
        if not text:
            return None, None

        # 解析 "x/y" 或 "x" 格式
        # OCR 可能识别为 "5/10"、"5/70"、"x5/10" 等
        nums = re.findall(r"\d+", text)
        if len(nums) >= 2:
            count, owned = int(nums[0]), int(nums[1])
            logger.debug(f"数量识别: {text!r} -> count={count}, owned={owned}")
            return count, owned
        elif len(nums) == 1:
            count = int(nums[0])
            logger.debug(f"数量识别: {text!r} -> count={count}, owned=None")
            return count, None

        logger.debug(f"数量识别: 无法从 {text!r} 中提取数字")
        return None, None

    # ─── OCR 辅助 ──────────────────────────────────────────

    def _ocr_single(self, crop: np.ndarray) -> str:
        """对小区域做 OCR，返回拼接文本"""
        results = self._ocr.recognize(crop)
        if not results:
            return ""
        return " ".join(r.text for r in results)

    # ─── 工具方法 ──────────────────────────────────────────

    def list_types(self) -> list[str]:
        """返回参考库中所有材料标识"""
        return self._db.get_labels()

    def reload(self):
        """强制重新加载参考库"""
        self._matcher.reload()
