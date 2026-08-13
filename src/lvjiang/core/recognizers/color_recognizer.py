"""颜色特征识别器（骨架）。

阶段 2 仅提供接口占位，后续实现基于 HSV 直方图 / 颜色阈值检测。
"""
from __future__ import annotations

from typing import Any

import numpy as np

from ...i18n import tr
from ._registry import register_recognizer


@register_recognizer
class ColorRecognizer:
    """颜色特征识别器。

    名字：``color``
    """

    name = "color"

    def __init__(self) -> None:
        pass

    def recognize(self, image: np.ndarray, **kwargs: Any) -> Any:
        raise NotImplementedError(tr("颜色特征识别器尚未实现"))
