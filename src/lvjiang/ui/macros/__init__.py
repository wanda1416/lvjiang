"""宏录制模块 - 低精度 DSL 与高精度输入轨迹录制

低精度产物是可编辑的 click/drag/scroll/press/move/wait 指令；高精度产物
由一条 replay input_trace 指令和 workflows/lvtrace 配套文件组成。两种模式
都以画布为归一化基准，高精度轨迹由专用实时调度器回放。
"""

from .recorder import PRECISION_HIGH, PRECISION_LOW, MacroRecorder

__all__ = ["MacroRecorder", "PRECISION_HIGH", "PRECISION_LOW"]
