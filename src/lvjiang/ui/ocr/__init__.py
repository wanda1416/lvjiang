"""OCR 子包 - 识别测试对话框与可框选画布

``canvas`` 提供带缩放/框选/手柄拖拽的基础画布，``dialog`` 在其上做 OCR 识别
测试，``pick_canvas`` 是同一画布的取点/取色变体（脚本编辑器取参数用）。
"""

from .canvas import OCRBox, OCRCanvas
from .dialog import OCRDialog
from .pick_canvas import PickCanvas

__all__ = ["OCRBox", "OCRCanvas", "OCRDialog", "PickCanvas"]
