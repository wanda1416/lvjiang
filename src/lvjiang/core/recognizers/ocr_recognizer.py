"""通用 OCR 识别器（基于 RapidOCR）。

将 ``OCREngine`` 适配为 ``Recognizer`` 协议，供 DSL ``recognize`` 指令
与通用识别测试对话框使用。
"""
from __future__ import annotations

from typing import Any

import numpy as np

from ..ocr import OCREngine
from ._registry import register_recognizer


@register_recognizer
class OCRRecognizer:
    """通用 OCR 识别器。

    名字：``ocr``
    输入：BGR numpy 数组
    输出：``list[OCRResult]``（text / confidence / bbox）
    """

    name = "ocr"

    def __init__(self) -> None:
        self._engine: OCREngine | None = None

    def _ensure_engine(self) -> OCREngine:
        if self._engine is None:
            self._engine = OCREngine()
        return self._engine

    def recognize(self, image: np.ndarray, **kwargs: Any) -> Any:
        """对整张图执行 OCR。"""
        return self._ensure_engine().recognize(image)
