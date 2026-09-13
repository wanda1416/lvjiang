"""Read-only overview of effective task parameters for every user."""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.task_params import resolve_task_params
from ..i18n import tr
from .button_styles import apply_compact_button_style, apply_dialog_button_box_style


def _format_value(value) -> str:
    if isinstance(value, bool):
        return tr("是") if value else tr("否")
    if value is None:
        return tr("（空）")
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


class AllUserParamsDialog(QDialog):
    """Show each user's effective parameters and whether they are overridden."""

    def __init__(
        self,
        workflow_configs: list[dict],
        usernames: list[str],
        users_dir: Path | None,
        initial_workflow_id: str,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(tr("全部用户参数"))
        self.setModal(True)
        self.resize(720, 520)
        self._users = list(usernames)
        self._users_dir = users_dir
        self._configs = [
            cfg for cfg in workflow_configs
            if cfg.get("scope", "daily") == "daily"
        ]

        layout = QVBoxLayout(self)
        task_row = QHBoxLayout()
        task_row.setContentsMargins(0, 0, 0, 0)
        task_row.setSpacing(8)
        task_row.addWidget(QLabel(tr("任务")))
        self._task_combo = QComboBox()
        self._task_combo.setObjectName("all_user_params_task")
        for config in self._configs:
            self._task_combo.addItem(
                str(config.get("name") or config.get("id") or ""),
                str(config.get("id") or ""),
            )
        initial_index = self._task_combo.findData(initial_workflow_id)
        if initial_index >= 0:
            self._task_combo.setCurrentIndex(initial_index)
        task_row.addWidget(self._task_combo, 1)
        self._previous_task_button = QPushButton(tr("上一个"))
        self._previous_task_button.setObjectName("previous_task")
        self._next_task_button = QPushButton(tr("下一个"))
        self._next_task_button.setObjectName("next_task")
        for button in (self._previous_task_button, self._next_task_button):
            button.setAutoDefault(False)
            button.setFixedHeight(24)
        apply_compact_button_style(
            self._previous_task_button,
            self._next_task_button,
            variant="neutral",
        )
        self._previous_task_button.clicked.connect(
            lambda: self._step_task(-1))
        self._next_task_button.clicked.connect(lambda: self._step_task(1))
        task_row.addWidget(self._previous_task_button)
        task_row.addWidget(self._next_task_button)
        layout.addLayout(task_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._user_tabs = QListWidget()
        self._user_tabs.setObjectName("all_user_params_users")
        self._user_tabs.setProperty("navigation", True)
        self._user_tabs.setMinimumWidth(110)
        self._user_tabs.setMaximumWidth(220)
        self._pages = QStackedWidget()
        self._user_tabs.currentRowChanged.connect(self._pages.setCurrentIndex)
        splitter.addWidget(self._user_tabs)
        splitter.addWidget(self._pages)
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([140, 580])
        layout.addWidget(splitter, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        apply_dialog_button_box_style(buttons)
        layout.addWidget(buttons)

        self._task_combo.currentIndexChanged.connect(self._on_task_changed)
        self._refresh_task_buttons()
        self._rebuild_users()

    def _on_task_changed(self, *_args) -> None:
        self._refresh_task_buttons()
        self._rebuild_users()

    def _step_task(self, offset: int) -> None:
        target = self._task_combo.currentIndex() + offset
        if 0 <= target < self._task_combo.count():
            self._task_combo.setCurrentIndex(target)

    def _refresh_task_buttons(self) -> None:
        index = self._task_combo.currentIndex()
        self._previous_task_button.setEnabled(index > 0)
        self._next_task_button.setEnabled(
            index >= 0 and index < self._task_combo.count() - 1)

    def _selected_config(self) -> dict | None:
        workflow_id = self._task_combo.currentData()
        return next(
            (cfg for cfg in self._configs if str(cfg.get("id")) == workflow_id),
            None,
        )

    def _rebuild_users(self, *_args) -> None:
        self._user_tabs.clear()
        while self._pages.count():
            widget = self._pages.widget(0)
            self._pages.removeWidget(widget)
            if widget is not None:
                widget.deleteLater()

        config = self._selected_config()
        if config is None:
            return
        workflow_id = str(config.get("id") or "")
        definitions = config.get("parameters", [])
        for username in self._users:
            values, source = resolve_task_params(
                workflow_id, username, definitions, self._users_dir)
            item = QListWidgetItem(
                f"{username} *" if source == "user" else username)
            item.setData(Qt.ItemDataRole.UserRole, username)
            item.setToolTip(
                tr("该用户使用独立参数") if source == "user"
                else tr("该用户使用全局参数"))
            if source == "user":
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self._user_tabs.addItem(item)
            self._pages.addWidget(
                self._build_user_page(username, definitions, values, source))
        if self._user_tabs.count():
            self._user_tabs.setCurrentRow(0)

    def _build_user_page(
        self,
        username: str,
        definitions: list[dict],
        values: dict,
        source: str,
    ) -> QWidget:
        page = QWidget()
        page.setProperty("username", username)
        outer = QVBoxLayout(page)

        independent = source == "user"
        source_label = QLabel(
            tr("参数来源：独立参数") if independent else tr("参数来源：全局参数"))
        source_label.setObjectName("parameter_source")
        source_label.setProperty("source", source)
        source_label.setStyleSheet(
            "font-weight: 600; color: palette(highlight);"
            if independent else "font-weight: 600;"
        )
        outer.addWidget(source_label)

        explanation = QLabel(
            tr("该用户已保存独立参数，任务运行时优先使用下列值。")
            if independent else
            tr("该用户未设置独立参数，任务运行时使用全局参数。"))
        explanation.setWordWrap(True)
        outer.addWidget(explanation)

        body = QWidget()
        form = QFormLayout(body)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        labels = {
            str(item.get("name")): str(item.get("label") or item.get("name"))
            for item in definitions
            if isinstance(item, dict) and item.get("name")
        }
        if values:
            for name, value in values.items():
                value_label = QLabel(_format_value(value))
                value_label.setObjectName(f"parameter_value_{name}")
                value_label.setWordWrap(True)
                value_label.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse)
                form.addRow(labels.get(name, name) + "：", value_label)
        else:
            form.addRow(QLabel(tr("该任务没有可配置参数")))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        return page


__all__ = ["AllUserParamsDialog"]
