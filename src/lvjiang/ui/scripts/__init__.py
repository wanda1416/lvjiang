"""脚本子包 - .wf 脚本的录制、编辑调试与清单配置

三块互不依赖的入口：录制对话框把用户操作翻译成 DSL 文本（录制引擎本身在
``core.macro_recorder``，与 Qt 无关）；编辑器负责语法高亮、校验与单步调试；
配置对话框管理脚本发现范围与日常页显示偏好。
"""

from .config_dialog import ScriptConfigDialog
from .editor_dialog import ScriptEditorDialog
from .record_dialog import ScriptRecordDialog

__all__ = ["ScriptConfigDialog", "ScriptEditorDialog", "ScriptRecordDialog"]
