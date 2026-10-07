"""任务历史文件的只读内嵌预览。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from loguru import logger
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from .button_styles import apply_button_style
from .combo_box import AutoWidthComboBox, ComboWidthMode

# 预览读取有界，避免长任务的日志一次性占满 UI 内存。
_PREVIEW_BYTES = 2 * 1024 * 1024
_LOG_HEADER = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} \| (\w+)\s* \| ")


def filter_log(text: str, minimum: int) -> str:
    """多行消息与 traceback 跟随其日志首行过滤，未知格式保留。"""
    lines = []
    visible = True
    for line in text.splitlines(keepends=True):
        match = _LOG_HEADER.match(line)
        if match:
            try:
                visible = logger.level(match[1]).no >= minimum
            except ValueError:
                visible = True
        if visible:
            lines.append(line)
    return "".join(lines)


def read_preview(path: Path | None, *, tail: bool = False) -> tuple[str, str]:
    if path is None:
        return "", tr("没有对应文件")
    try:
        with path.open("rb") as file:
            size = file.seek(0, 2)
            offset = max(0, size - _PREVIEW_BYTES) if tail else 0
            file.seek(offset)
            if offset:
                file.readline()  # 跳过截断的 UTF-8/半行。
            content = file.read(_PREVIEW_BYTES).decode("utf-8-sig", errors="replace")
        notice = ""
        if size > _PREVIEW_BYTES:
            notice = tr("文件较大，仅预览末尾 2 MiB") if tail else tr("文件较大，仅预览开头 2 MiB")
        return content, notice
    except OSError as exc:
        return "", tr("无法读取文件") + f"：{exc}"


class HistoryViewer(QWidget):
    """切换记录不修改源文件；日志级别只改变显示。"""

    def __init__(self, *, batch: bool = False):
        super().__init__()
        self._batch = batch
        self._log_path: Path | None = None
        self._log_text = ""
        self._log_notice = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        if not batch:
            log_page = QWidget()
            log_layout = QVBoxLayout(log_page)
            controls = QHBoxLayout()
            controls.addWidget(QLabel(tr("日志级别")))
            self.level = AutoWidthComboBox(width_mode=ComboWidthMode.FULL)
            self.level.addItem(tr("全部"), 0)
            for name in ("DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"):
                self.level.addItem(name, logger.level(name).no)
            self.level.setCurrentIndex(self.level.findText("INFO"))
            self.level.setToolTip(tr("显示所选级别及更高级别的日志"))
            self.level.currentIndexChanged.connect(self._render_log)
            controls.addWidget(self.level)
            controls.addStretch(1)
            refresh = QPushButton(tr("刷新日志"))
            apply_button_style(refresh, variant="neutral")
            refresh.clicked.connect(self.refresh_log)
            controls.addWidget(refresh)
            log_layout.addLayout(controls)
            self.log_notice = QLabel()
            self.log_notice.setWordWrap(True)
            log_layout.addWidget(self.log_notice)
            self.log = self._text_view()
            log_layout.addWidget(self.log, 1)
            self.tabs.addTab(log_page, tr("执行日志"))
        self.content = self._text_view()
        self.tabs.addTab(self.content, tr("批量报告") if batch else tr("执行结果"))
        self.details = self._text_view()
        self.tabs.addTab(self.details, tr("记录详情"))
        layout.addWidget(self.tabs)
        self.clear()

    @staticmethod
    def _text_view() -> QPlainTextEdit:
        edit = QPlainTextEdit()
        edit.setReadOnly(True)
        return edit

    def set_record(self, *, content: Path | None, details: str, log: Path | None = None) -> None:
        self._log_path = log
        self.details.setPlainText(details)
        if not self._batch:
            self.refresh_log()
        text, notice = read_preview(content)
        if content and content.suffix.lower() == ".json" and not notice:
            try:
                text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
            except ValueError:
                notice = tr("结果不是有效 JSON，显示原文")
        self.content.setPlainText((notice + "\n\n" if notice else "") + text)

    def refresh_log(self) -> None:
        self._log_text, self._log_notice = read_preview(self._log_path, tail=True)
        self._render_log()

    def _render_log(self) -> None:
        text = filter_log(self._log_text, int(self.level.currentData() or 0))
        self.log.setPlainText(text)
        notice = self._log_notice
        if not text and not notice:
            notice = tr("当前级别没有日志") if self._log_text else tr("日志文件为空")
        self.log_notice.setText(notice)
        self.log_notice.setVisible(bool(notice))

    def clear(self) -> None:
        self._log_path = None
        self._log_text = ""
        self.content.setPlainText(tr("选择一条记录查看内容"))
        self.details.clear()
        if not self._batch:
            self._log_notice = tr("选择一条记录查看日志")
            self._render_log()

    def show_content(self) -> None:
        self.tabs.setCurrentWidget(self.content)
