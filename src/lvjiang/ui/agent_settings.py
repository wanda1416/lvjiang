"""AI 设置中的外部 Agent 目录编辑器；编辑草稿不改变运行中的接入。"""
from __future__ import annotations

from uuid import uuid4

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.agent_settings import AgentSettings, load_agent_settings
from ..core.config.resolver import save_app_config_node
from ..i18n import tr
from .button_styles import apply_button_style


class AgentSettingsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        note = QLabel(tr("配置外部 Agent 的名称和默认导出文件，统一使用 Streamable HTTP 接入。智能调律中选择使用哪个 Agent；接入令牌不会保存在这里。"))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([tr("Agent 名称"), tr("默认导出文件")])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.table.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        form = QFormLayout()
        self.download = QLineEdit()
        form.addRow(tr("下载 Agent 链接"), self.download)
        layout.addLayout(form)
        row = QHBoxLayout()
        self.add_button = QPushButton(tr("添加 Agent"))
        self.remove_button = QPushButton(tr("删除所选"))
        self.save_button = QPushButton(tr("保存 Agent 设置"))
        for button in (self.add_button, self.remove_button, self.save_button):
            apply_button_style(button, variant="action" if button is self.save_button else "neutral")
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.add_button.clicked.connect(lambda: self.add_row())
        self.remove_button.clicked.connect(self.remove_selected)
        self.save_button.clicked.connect(self.save)
        try:
            settings = load_agent_settings()
            for item in settings.items:
                self.add_row(item.key, item.name, item.export_path)
            self.download.setText(settings.download_url)
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))

    def add_row(self, key=None, name="", path=""):
        row = self.table.rowCount()
        self.table.insertRow(row)
        label = QTableWidgetItem(name)
        label.setData(Qt.ItemDataRole.UserRole, key or uuid4().hex)
        self.table.setItem(row, 0, label)
        self.table.setItem(row, 1, QTableWidgetItem(path))

    def remove_selected(self):
        for row in sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(row)

    def save(self):
        try:
            items = []
            for row in range(self.table.rowCount()):
                name, path = self.table.item(row, 0), self.table.item(row, 1)
                assert name is not None and path is not None
                items.append({"key": name.data(Qt.ItemDataRole.UserRole), "name": name.text(),
                              "export_path": path.text()})
            settings = AgentSettings.from_dict({"items": items, "download_agent": {"url": self.download.text()}})
            save_app_config_node("agents", settings.to_dict())
            self.status.setText(tr("Agent 设置已保存"))
        except (ValueError, OSError, RuntimeError) as exc:
            self.status.setText(str(exc))
