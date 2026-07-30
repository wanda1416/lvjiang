"""工作流基类包 - 提供游戏操作能力

子类可以：
1. 纯 Python 实现：直接重写 run() 方法
2. DSL 驱动：由 WorkflowEngine 加载 .wf 文件执行，操作委托给本实例

所有游戏操作按职责拆分为 Mixin，由 BaseWorkflow 组合（见 core.py）：
- recognition.py  截图/OCR/材料识别与 by 短路识别
- actions.py      点击/point/arrow/等待
- coords.py       归一化坐标 → 屏幕坐标换算
- panel.py        panel 对齐与格子级操作
"""
from .core import BaseWorkflow

__all__ = ["BaseWorkflow"]
