"""材料分类器 - 识别材料槽中的材料类型、等级和数量

识别策略：
- 类型识别：使用通用 ReferenceMatcher（ORB 特征匹配）
- 输出元数据 OCR：按 references.yaml meta_schema 中 scope=output 字段的
  crop 区域裁剪 OCR（schema 未配置时回退硬编码上下半区）
- 空槽检测：图像方差判断（游戏专属）

通用识别只产出 ocr_texts 原始文本（输出字段 key -> 文本）。
投入/持有的语义解析由调用方（如调律流程）按需处理。

参考库数据源：ReferenceDatabase（references.yaml）。
"""

import re
from dataclasses import dataclass, field

import cv2
import numpy as np
from loguru import logger

from lvjiang.core.ocr import OCREngine
from lvjiang.core.recognizers.reference_matcher import ReferenceMatcher
from lvjiang.core.reference_db import ReferenceDatabase

# yysls 调律输出字段契约：自动调律启动前必须存在的 output 字段 key
# （业务层对核心层 meta_schema 的合法要求，非侵入）
REQUIRED_OUTPUT_FIELDS = ("level_text", "count_text")


def get_missing_output_fields(db: ReferenceDatabase) -> list[str]:
    """返回当前图库空间缺失的必需输出字段 key（空列表 = 满足启动条件）"""
    existing = {f.key for f in db.get_output_fields()}
    return [k for k in REQUIRED_OUTPUT_FIELDS if k not in existing]


def _parse_number(text: str) -> int | None:
    """解析数字文本，支持 '123'、'1.5万'、'12万' 等格式（yysls 游戏专属）

    含多段数字时取最后一段（OCR 噪声如 '0/1 1092' 取 1092）
    """
    text = text.strip()
    if not text:
        return None
    multiplier = 1
    if text.endswith("万"):
        text = text[:-1]
        multiplier = 10000
    try:
        return int(float(text) * multiplier)
    except ValueError:
        nums = re.findall(r"\d+", text)
        return int(nums[-1]) * multiplier if nums else None


@dataclass
class MaterialInfo:
    """单个材料槽识别结果（游戏专属）

    通用字段：
        type: 材料类型（如 "定音石"），空槽为 ""
        ocr_texts: 输出元数据 OCR 原始文本（输出字段 key -> 文本，
            如 {"level_text": "110阶", "count_text": "0/691"}）
        confidence: 类型匹配置信度 0~1
        meta: 匹配参考条目的元数据（输入字段 key -> 值，如 {"level": 110}）

    便捷属性（读取 ocr_texts 原始文本）：
        level_text: 等级区域的 OCR 文本（如 "110阶"）
        count_text: 数量区域的 OCR 文本（如 "0/691" 或 "691"）

    解析属性（从文本解析）：
        real_level: 从 level_text 解析的实际等级（OCR 读出的真实等级，
            可能与参考图库标注的 level 不同——不同等级材料外观相似时
            参考图匹配可能出错，OCR 读出的 real_level 更可靠）
        count: 用户拥有的数量（核心语义）— 有 "/" 时取后者，无 "/" 时取整个数字
        devoted: 投入数量（仅调律流程关注）— 有 "/" 时取前者，无 "/" 时为 None
    """
    type: str
    ocr_texts: dict[str, str] = field(default_factory=dict)
    confidence: float = 0.0
    meta: dict = field(default_factory=dict)

    # ── 便捷属性（读取 ocr_texts）──────────────────────

    @property
    def level_text(self) -> str:
        return self.ocr_texts.get("level_text", "")

    @property
    def count_text(self) -> str:
        return self.ocr_texts.get("count_text", "")

    # ── 解析属性 ─────────────────────────────────────────────

    @property
    def real_level(self) -> int | None:
        """从 level_text 解析的实际等级（OCR 读出的真实等级）

        支持 '110'、'110阶'、'1.5万' 等格式。
        注意：参考图库标注的 meta['level'] 可能因外观相似而匹配错误，
        real_level 是 OCR 从画面直接读出的等级，更可靠。
        """
        return _parse_number(self.level_text)

    @property
    def count(self) -> int | None:
        """用户拥有的数量（核心语义）

        "0/691" → 691, "691" → 691, "1.5万" → 15000, "" → None
        """
        if "/" in self.count_text:
            parts = self.count_text.split("/")
            return _parse_number(parts[-1]) if parts else None
        return _parse_number(self.count_text)

    @property
    def devoted(self) -> int | None:
        """投入数量（仅调律流程关注）

        "0/691" → 0, "691" → None, "" → None
        """
        if "/" not in self.count_text:
            return None
        parts = self.count_text.split("/")
        return _parse_number(parts[0]) if parts else None


class MaterialRecognizer:
    """材料槽内容识别器（游戏专属）

    使用通用 ReferenceMatcher 做类型识别，
    按 schema 输出元数据的 crop 区域 OCR 产出原始文本（schema 无输出字段
    时回退硬编码上下半区）。

    用法：
        recognizer = MaterialRecognizer(ocr_engine)
        result = recognizer.recognize(slot_img)
        # result.type -> "定音石"
        # result.ocr_texts -> {"level_text": "110阶", "count_text": "0/691"}
        # result.level_text -> "110阶"
        # result.count_text -> "0/691"
        # result.real_level -> 110   (从 level_text 解析的实际等级)
        # result.count -> 691   (用户拥有的数量，核心语义)
        # result.devoted -> 0   (投入，仅调律流程关注)
    """

    # ── 子区域回退配置（schema 无输出字段时使用）──────────────────────────────
    # 等级文字区域（上半部分，schema 无输出字段时回退使用）
    LEVEL_REGION = (0.0, 0.0, 1.0, 0.50)
    # 数量文字区域（下半部分，schema 无输出字段时回退使用）
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
            MaterialInfo（通用字段：type, ocr_texts, confidence）
        """
        # 1. 空槽检测
        if self._is_empty(slot_img):
            return MaterialInfo(type="", confidence=1.0)

        # 2. 类型识别（通用 ReferenceMatcher）
        match_result = self._matcher.match(slot_img, group=group)
        mat_type = match_result.label
        confidence = match_result.confidence

        if not mat_type:
            return MaterialInfo(type="", confidence=confidence)

        # 3. 输出元数据 OCR（按 schema 输出字段的 crop 区域）
        ocr_texts = self._ocr_output_fields(slot_img)

        return MaterialInfo(
            type=mat_type,
            ocr_texts=ocr_texts,
            confidence=confidence,
            meta=match_result.meta,
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
            return [MaterialInfo(type="", confidence=1.0)]

        # 2. 类型识别（通用 ReferenceMatcher top N）
        match_results = self._matcher.match_top_n(slot_img, n=n, group=group)

        if not match_results:
            return []

        # 3. 输出元数据 OCR — 只识别一次
        ocr_texts = self._ocr_output_fields(slot_img)

        # 4. 为每个匹配结果构建 MaterialInfo，等级用参考条目自身的标记
        results = []
        for match_result in match_results:
            texts = dict(ocr_texts)
            # 从参考条目元数据获取原始等级（区分同名不同等级）
            ref_level = match_result.meta.get("level")
            if ref_level is not None:
                texts["level_text"] = f"{ref_level}阶"

            results.append(MaterialInfo(
                type=match_result.label,
                ocr_texts=texts,
                confidence=match_result.confidence,
                meta=match_result.meta,
            ))

        return results

    # ─── 富 dict 序列化（插件扩展钩子）────────────────────────

    @staticmethod
    def build_rich_base(info: MaterialInfo, group: str | None = None) -> dict:
        """构建 rich 模式 base dict（扁平结构）

        字段：label / group / confidence / level_text / count_text
        np.float32 → Python float 确保 JSON 可序列化。
        workflows 层和 enrich_info 共用此方法，消除重复。
        """
        return {
            "label": info.type,
            "group": group or "",
            "confidence": float(info.confidence),
            "level_text": info.ocr_texts.get("level_text", ""),
            "count_text": info.ocr_texts.get("count_text", ""),
        }

    def enrich_info(self, info: MaterialInfo, group: str | None = None) -> dict:
        """将 MaterialInfo 转为富 dict，含插件专属解析字段。

        核心提供扁平 base 字段（label/group/confidence/level_text/count_text），
        通过内置函数 yysls_rich_parse 追加游戏专属的解析字段。

        DSL ``recognize ... as rich $var`` 使用此方法构建返回值。
        """
        from lvjiang.workflows import builtins
        base = self.build_rich_base(info, group=group)
        parser = builtins.get_function("yysls_rich_parse")
        return parser(base) if parser else base

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

    # ─── 输出元数据 OCR ─────────────────────────────────────

    def _ocr_output_fields(self, slot_img: np.ndarray) -> dict[str, str]:
        """按 schema 输出字段的 crop 区域逐个 OCR，返回 {key: 文本}

        schema 无输出字段时回退硬编码上下半区，产出 key 仍为
        level_text / count_text（兼容旧配置）。
        """
        output_fields = self._db.get_output_fields()
        if not output_fields:
            return {
                "level_text": self._ocr_region(slot_img, self.LEVEL_REGION),
                "count_text": self._ocr_region(slot_img, self.COUNT_REGION),
            }
        return {
            f.key: self._ocr_region(slot_img, tuple(f.crop))  # type: ignore[arg-type]
            for f in output_fields
        }

    def _ocr_region(self, slot_img: np.ndarray, region: tuple) -> str:
        """OCR 识别指定归一化区域 (x, y, w, h) 的文本"""
        h, w = slot_img.shape[:2]
        x1 = int(region[0] * w)
        y1 = int(region[1] * h)
        x2 = int((region[0] + region[2]) * w)
        y2 = int((region[1] + region[3]) * h)

        crop = slot_img[y1:y2, x1:x2]
        if crop.size == 0:
            return ""

        text = self._ocr_single(crop)
        if text:
            logger.debug(f"输出区域 OCR {region}: {text!r}")
        return text.strip()

    # ─── OCR 辅助 ──────────────────────────────────────────

    def _ocr_single(self, crop: np.ndarray) -> str:
        """对小区域做 OCR，返回拼接文本"""
        results = self._ocr.recognize(crop)
        if not results:
            return ""
        return " ".join(r.text for r in results)

    # ─── 工具方法 ──────────────────────────────────────────

    def reload(self):
        """强制重新加载参考库"""
        self._matcher.reload()
