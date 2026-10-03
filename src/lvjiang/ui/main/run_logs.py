"""主界面实时日志的结构化事件。"""
from __future__ import annotations

import logging
from dataclasses import dataclass


@dataclass(frozen=True)
class RunLogEvent:
    level: int
    text: str
    task_run_id: str = ""
    target_id: str = ""
    target_label: str = ""

    @classmethod
    def from_text(
        cls, text: str, *, task_run_id: str = "", target_id: str = "",
        target_label: str = "",
    ) -> "RunLogEvent":
        prefix = text[:40]
        if text.startswith("[ERROR]") or "| ERROR" in prefix:
            level = logging.ERROR
        elif text.startswith("[WARNING]") or "| WARNING" in prefix:
            level = logging.WARNING
        elif text.startswith("[DEBUG]") or "| DEBUG" in prefix:
            level = logging.DEBUG
        else:
            level = logging.INFO
        return cls(level, text, task_run_id, target_id, target_label)

    def display_text(self) -> str:
        """返回实时合流视图使用的带目标标识文本。"""
        if not self.task_run_id:
            return self.text
        target = self.target_label or self.target_id or "—"
        return f"[{target} · {self.task_run_id[:8]}] {self.text}"
