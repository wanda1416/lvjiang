"""模板匹配识别器（骨架）。

阶段 2 仅提供接口占位，后续实现基于 OpenCV ``matchTemplate``。
"""
from __future__ import annotations

from typing import Any

import numpy as np

from ._registry import register_recognizer


@register_recognizer
class TemplateRecognizer:
    """模板匹配识别器。

    名字：``template``
    """

    name = "template"

    def __init__(self, template_path: str | None = None, threshold: float = 0.8) -> None:
        self.template_path = template_path
        self.threshold = threshold

    def recognize(self, image: np.ndarray, **kwargs: Any) -> Any:
        raise NotImplementedError("模板匹配识别器尚未实现")
