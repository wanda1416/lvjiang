"""yysls 应用事件主题常量。

UI（``ui/events.py`` 的 Qt 适配器）与工作流内置函数都从这里取主题名：
工作流层不应为了一个字符串而 import UI 包。
"""
from __future__ import annotations

APP_ID = "yysls"
EQUIPMENT_CHANGED = "equipment.changed"
OPEN_PLAY_STYLE_FORM = "play_style.open_form"
GRADUATION_UPDATED = "graduation.updated"
