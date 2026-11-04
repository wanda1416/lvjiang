"""主界面实时日志的结构化事件。"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

#: UI 直写消息补的时间戳前缀，与 loguru sink 的
#: ``{time:HH:mm:ss} | {level:<7} | `` 保持同一格式，两种来源在面板里混排
#: 才不会一眼看出是谁写的。
_UI_STAMP = "{time} | {level:<7} | "

_LEVEL_TEXT = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
}


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

    @classmethod
    def ui_message(
        cls, text: str, *, task_run_id: str = "", target_id: str = "",
        target_label: str = "",
    ) -> "RunLogEvent":
        """UI 代码直写的消息（log_text.append / notice.emit）。

        这些消息不经过 loguru，面板里会缺时间戳与级别，与 logger 输出混排
        时格式断裂；这里统一补上与 sink 相同的前缀。已有的 loguru 风格前缀
        原样保留（重复补缀没有意义）。
        """
        if "| INFO" in text[:40] or "| WARNING" in text[:40] \
                or "| ERROR" in text[:40] or "| DEBUG" in text[:40]:
            return cls.from_text(text, task_run_id=task_run_id,
                                 target_id=target_id, target_label=target_label)
        stamp = _UI_STAMP.format(
            time=datetime.now().strftime("%H:%M:%S"),
            level=_LEVEL_TEXT.get(logging.INFO, "INFO"))
        return cls.from_text(f"{stamp}{text}", task_run_id=task_run_id,
                             target_id=target_id, target_label=target_label)

    def display_text(self) -> str:
        """返回实时合流视图使用的带目标标识文本。

        运行 ID 用 ` - ` 而不是 ` · ` 接在后面：` · ` 是目标名称内部的分隔符
        （型号 · 连接地址），两级用同一个符号就看不出哪一段才是任务。
        """
        if not self.task_run_id:
            return self.text
        target = self.target_label or self.target_id or "—"
        return f"[{target} - {self.task_run_id[:8]}] {self.text}"
