"""材料分类器 - 识别材料槽中的材料类型、等级和数量

识别策略：
- 类型识别：使用通用 ReferenceMatcher（ORB 特征匹配）
- 等级识别：OCR 裁剪上半部分（游戏专属）
- 数量识别：OCR 裁剪下半部分（游戏专属）
- 空槽检测：图像方差判断（游戏专属）

通用识别只产出 level_text / count_text 原始文本。
投入/持有的语义解析由调用方（如调律流程）按需处理。

参考库数据源：ReferenceDatabase（references.yaml）。
"""

import re
from dataclasses import dataclass

import cv2
import numpy as np
from loguru import logger

from lvjiang.core.ocr import OCREngine
from lvjiang.core.recognizers.reference_matcher import ReferenceMatcher
from lvjiang.core.reference_db import ReferenceDatabase


@dataclass
class MaterialInfo:
    """单个材料槽识别结果（游戏专属）

    通用字段：
        type: 材料类型（如 "定音石"），空槽为 ""
        level_text: 上半部分 OCR 原始文本（如 "110阶"）
        count_text: 下半部分 OCR 原始文本（如 "0/691" 或 "691"）
        confidence: 类型匹配置信度 0~1

    解析属性（从 text 解析）：
        level: 从 level_text 提取的第一个数字
        count: 用户拥有的数量（核心语义）— 有 "/" 时取后者，无 "/" 时取整个数字
        devoted: 投入数量（仅调律流程关注）— 有 "/" 时取前者，无 "/" 时为 None
    """
    type: str
    level_text: str = ""
    count_text: str = ""
    confidence: float = 0.0

    # ── 解析属性 ─────────────────────────────────────────────

    @property
    def level(self) -> int | None:
        """从 level_text 提取第一个数字"""
        nums = re.findall(r"\d+", self.level_text)
        return int(nums[0]) if nums else None

    @property
    def count(self) -> int | None:
        """用户拥有的数量（核心语义）

        "0/691" → 691, "691" → 691, "" → None
        """
        nums = re.findall(r"\d+", self.count_text)
        if "/" in self.count_text:
            return int(nums[1]) if len(nums) >= 2 else None
        return int(nums[0]) if nums else None

    @property
    def devoted(self) -> int | None:
        """投入数量（仅调律流程关注）

        "0/691" → 0, "691" → None, "" → None
        """
        if "/" not in self.count_text:
            return None
        nums = re.findall(r"\d+", self.count_text)
        return int(nums[0]) if nums else None


class MaterialRecognizer:
    """材料槽内容识别器（游戏专属）

    使用通用 ReferenceMatcher 做类型识别，
    OCR 分上下两半：上半识别等级文本，下半识别数量文本。

    用法：
        recognizer = MaterialRecognizer(ocr_engine)
        result = recognizer.recognize(slot_img)
        # result.type -> "定音石"
        # result.level_text -> "110阶"
        # result.count_text -> "0/691"
        # result.level -> 110   (从 level_text 解析)
        # result.count -> 691   (用户拥有的数量，核心语义)
        # result.devoted -> 0   (投入，仅调律流程关注)
    """

    # ── 子区域比例配置（游戏专属）──────────────────────────────
    # 等级文字区域（上半部分）
    LEVEL_REGION = (0.0, 0.0, 1.0, 0.50)
    # 数量文字区域（下半部分）
    COUNT_REGION = (0.0, 0.50, 1.0, 0.50)
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
            MaterialInfo（通用字段：type, level_text, count_text, confidence）
        """
        # 1. 空槽检测
        if self._is_empty(slot_img):
            return MaterialInfo(type="", level_text="", count_text="", confidence=1.0)

        # 2. 类型识别（通用 ReferenceMatcher）
        match_result = self._matcher.match(slot_img, group=group)
        mat_type = match_result.label
        confidence = match_result.confidence

        if not mat_type:
            return MaterialInfo(type="", level_text="", count_text="", confidence=confidence)

        # 3. 等级 OCR（上半部分）
        level_text = self._ocr_level(slot_img)

        # 4. 数量 OCR（下半部分）
        count_text = self._ocr_count(slot_img)

        return MaterialInfo(
            type=mat_type,
            level_text=level_text,
            count_text=count_text,
            confidence=confidence,
        )

    def recognize_top_n(
        self,
        slot_img: np.ndarray,
        n: int = 5,
        group: str | None = None,
    ) -> list[MaterialInfo]:
        """识别单个材料槽，返回最相似的 N 个结果

        Args:
            slot_img: 材料槽裁剪图（BGR numpy 数组）
            n: 返回结果数量
            group: 限定匹配范围到指定分组

        Returns:
            按置信度降序排列的 MaterialInfo 列表
        """
        # 1. 空槽检测
        if self._is_empty(slot_img):
            return [MaterialInfo(type="", level_text="", count_text="", confidence=1.0)]

        # 2. 类型识别（通用 ReferenceMatcher top N）
        match_results = self._matcher.match_top_n(slot_img, n=n, group=group)

        if not match_results:
            return []

        # 3. 数量 OCR（下半部分）— 只识别一次
        count_text = self._ocr_count(slot_img)

        # 4. 为每个匹配结果构建 MaterialInfo，使用参考条目自身的等级
        results = []
        for match_result in match_results:
            # 从参考条目元数据获取原始等级
            ref_level = match_result.meta.get("level")
            if ref_level is not None:
                level_text = f"{ref_level}阶"
            else:
                level_text = ""  # 无等级信息

            results.append(MaterialInfo(
                type=match_result.label,
                level_text=level_text,
                count_text=count_text,
                confidence=match_result.confidence,
            ))

        return results

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

    # ─── 等级 OCR（上半部分）───────────────────────────────────

    def _ocr_level(self, slot_img: np.ndarray) -> str:
        """OCR 识别上半部分等级文本"""
        h, w = slot_img.shape[:2]
        x1 = int(self.LEVEL_REGION[0] * w)
        y1 = int(self.LEVEL_REGION[1] * h)
        x2 = int((self.LEVEL_REGION[0] + self.LEVEL_REGION[2]) * w)
        y2 = int((self.LEVEL_REGION[1] + self.LEVEL_REGION[3]) * h)

        crop = slot_img[y1:y2, x1:x2]
        if crop.size == 0:
            return ""

        text = self._ocr_single(crop)
        if text:
            logger.debug(f"等级 OCR: {text!r}")
        return text.strip()

    # ─── 数量 OCR（下半部分）───────────────────────────────────

    def _ocr_count(self, slot_img: np.ndarray) -> str:
        """OCR 识别下半部分数量文本

        返回原始文本（如 "0/691" 或 "691"），由调用方按需解析。
        """
        h, w = slot_img.shape[:2]
        x1 = int(self.COUNT_REGION[0] * w)
        y1 = int(self.COUNT_REGION[1] * h)
        x2 = int((self.COUNT_REGION[0] + self.COUNT_REGION[2]) * w)
        y2 = int((self.COUNT_REGION[1] + self.COUNT_REGION[3]) * h)

        crop = slot_img[y1:y2, x1:x2]
        if crop.size == 0:
            return ""

        text = self._ocr_single(crop)
        if text:
            logger.debug(f"数量 OCR: {text!r}")
        return text.strip()

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
