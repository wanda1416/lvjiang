"""颜色特征识别器 —— 把 color_ops 图色原语包成 Recognizer 协议

算法本体在 ``color_ops``（纯 numpy，可离线回归）；这一层只做 op 分派，
让通用识别测试对话框能按名字 ``color`` 列出并调用。DSL 工作流走
``workflows.builtins.vision`` 的内置函数，不经过这里。
"""
from __future__ import annotations

from typing import Any, Callable

import numpy as np

from ...i18n import tr
from . import color_ops
from ._registry import register_recognizer

#: 支持的 op → color_ops 函数。kwargs 原样透传，坐标按像素给。
_OPS: dict[str, Callable[..., Any]] = {
    "pixel": color_ops.pixel_rgb,
    "bright": color_ops.brightness,
    "ratio": color_ops.color_ratio,
    "ratio_tol": color_ops.color_ratio_tol,
    "segs": color_ops.bright_segments,
    "vec": color_ops.color_vec,
    "multi": color_ops.find_multi_color,
    "icons": color_ops.find_icons,
}


@register_recognizer
class ColorRecognizer:
    """颜色特征识别器。

    名字：``color``

    用法：``recognize(image, op="ratio", x1=..., y1=..., x2=..., y2=..., lo=(..), hi=(..))``
    """

    name = "color"

    def __init__(self) -> None:
        pass

    def recognize(self, image: np.ndarray, op: str = "ratio", **kwargs: Any) -> Any:
        fn = _OPS.get(op)
        if fn is None:
            raise ValueError(tr("未知图色操作: {op}，可用: {ops}").format(
                op=op, ops=", ".join(_OPS)))
        return fn(image, **kwargs)
