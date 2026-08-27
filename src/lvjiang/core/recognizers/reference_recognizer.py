"""通用参考图识别：参考匹配与 schema 驱动的输出区域 OCR。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from loguru import logger

from ..ocr import OCREngine
from ..reference_db import REFERENCE_OUTPUT_RESERVED_KEYS, ReferenceDatabase
from .reference_matcher import MatchResult, ReferenceMatcher

RICH_RESERVED_KEYS = REFERENCE_OUTPUT_RESERVED_KEYS


@dataclass(slots=True)
class ReferenceInfo:
    """一次参考图识别结果；不包含任何 app 领域解析。"""

    label: str
    group: str = ""
    confidence: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)
    ocr_texts: dict[str, str] = field(default_factory=dict)


class ReferenceRecognizer:
    """使用 :class:`ReferenceMatcher` 匹配并按 meta_schema 执行 OCR。"""

    name = "reference"

    def __init__(
        self,
        ocr_engine: OCREngine,
        reference_db: ReferenceDatabase | None = None,
    ) -> None:
        self._ocr = ocr_engine
        self._db = reference_db or ReferenceDatabase()
        self._matcher = ReferenceMatcher(self._db)

    @property
    def reference_db(self) -> ReferenceDatabase:
        return self._db

    @property
    def matcher(self) -> ReferenceMatcher:
        return self._matcher

    def recognize(
        self,
        image: np.ndarray,
        group: str | list[str] | None = None,
    ) -> ReferenceInfo:
        if image is None or image.size == 0:
            return ReferenceInfo(label="")
        match = self._matcher.match(image, group=group)
        return self._build_info(image, match)

    def recognize_top_n(
        self,
        image: np.ndarray,
        n: int = 5,
        group: str | list[str] | None = None,
    ) -> list[ReferenceInfo]:
        if image is None or image.size == 0:
            return []
        matches = self._matcher.match_top_n(image, n=n, group=group)
        if not matches:
            return []
        # 所有候选来自同一画面，OCR 只执行一次且绝不由参考 meta 改写。
        ocr_texts = self._ocr_output_fields(image)
        return [self._build_info(image, match, ocr_texts) for match in matches]

    @staticmethod
    def build_rich_base(info: ReferenceInfo) -> dict[str, Any]:
        """构建可交给 DSL ``with <fn>`` 转换的稳定字典。"""
        return {
            **info.ocr_texts,
            "label": info.label,
            "group": info.group,
            "confidence": float(info.confidence),
            "meta": dict(info.meta),
        }

    def _build_info(
        self,
        image: np.ndarray,
        match: MatchResult,
        ocr_texts: dict[str, str] | None = None,
    ) -> ReferenceInfo:
        if not match.label:
            return ReferenceInfo(label="", confidence=float(match.confidence))
        return ReferenceInfo(
            label=match.label,
            group=match.entry.group if match.entry else "",
            confidence=float(match.confidence),
            meta=dict(match.meta),
            ocr_texts=(dict(ocr_texts) if ocr_texts is not None
                       else self._ocr_output_fields(image)),
        )

    def _ocr_output_fields(self, image: np.ndarray) -> dict[str, str]:
        return {
            field.key: self._ocr_region(image, tuple(field.crop))  # type: ignore[arg-type]
            for field in self._db.get_output_fields()
        }

    def _ocr_region(self, image: np.ndarray, region: tuple[float, ...]) -> str:
        height, width = image.shape[:2]
        x1 = int(region[0] * width)
        y1 = int(region[1] * height)
        x2 = int((region[0] + region[2]) * width)
        y2 = int((region[1] + region[3]) * height)
        crop = image[y1:y2, x1:x2]
        if crop.size == 0:
            return ""
        results = self._ocr.recognize(crop)
        text = " ".join(result.text for result in results).strip() if results else ""
        if text:
            logger.debug(f"参考图输出区域 OCR {region}: {text!r}")
        return text

    def reload(self) -> None:
        self._db.load()
        self._matcher.reload()
