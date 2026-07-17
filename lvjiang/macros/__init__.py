"""宏录制模块 - 将鼠标操作录制为 DSL 语句文本

录制产物直接是符合 .wf 语法的 click/drag/wait 语句，
坐标为画布归一化比例，可剪切复用到任意工作流，回放走现有 DSL 引擎。
"""

from .recorder import MacroRecorder

__all__ = ["MacroRecorder"]
